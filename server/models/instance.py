#!/usr/bin/env python
# -*- coding: utf-8 -*-

import datetime
import ssl
import time
from urllib.request import urlopen
from urllib.error import HTTPError, URLError

from girder import logger
from girder.constants import AccessType, SortDir
from girder.exceptions import ValidationException
from girder.models.model_base import AccessControlledModel
from girder.models.token import Token
from girder.plugins.worker import getCeleryApp
from girder.plugins.jobs.constants import JobStatus, REST_CREATE_JOB_TOKEN_SCOPE
from gwvolman.tasks import \
    create_volume, launch_container, update_container, shutdown_container, \
    remove_volume, build_tale_image

from ..constants import InstanceStatus
from ..schema.misc import containerInfoSchema

from girder.plugins.wholetale.models.tale import Tale


TASK_TIMEOUT = 15.0
BUILD_TIMEOUT = 360.0


class Instance(AccessControlledModel):

    def initialize(self):
        self.name = 'instance'
        compoundSearchIndex = (
            ('taleId', SortDir.ASCENDING),
            ('creatorId', SortDir.DESCENDING),
            ('name', SortDir.ASCENDING)
        )
        self.ensureIndices([(compoundSearchIndex, {})])

        self.exposeFields(
            level=AccessType.READ,
            fields={'_id', 'created', 'creatorId', 'iframe', 'name', 'taleId'})
        self.exposeFields(
            level=AccessType.WRITE,
            fields={'containerInfo', 'lastActivity', 'status', 'url', 'sessionId'})

    def validate(self, instance):
        if not InstanceStatus.isValid(instance['status']):
            raise ValidationException(
                'Invalid instance status %s.' % instance['status'],
                field='status')
        return instance

    def list(self, user=None, tale=None, limit=0, offset=0,
             sort=None, currentUser=None):
        """
        List a page of jobs for a given user.

        :param user: The user who owns the job.
        :type user: dict or None
        :param limit: The page limit.
        :param offset: The page offset
        :param sort: The sort field.
        :param currentUser: User for access filtering.
        """
        cursor_def = {}
        if user is not None:
            cursor_def['creatorId'] = user['_id']
        if tale is not None:
            cursor_def['taleId'] = tale['_id']
        cursor = self.find(cursor_def, sort=sort)
        for r in self.filterResultsByPermission(
                cursor=cursor, user=currentUser, level=AccessType.READ,
                limit=limit, offset=offset):
            yield r

    def updateAndRestartInstance(self, instance, token, digest):
        """
        Updates and restarts an instance.

        :param image: The instance document to restart.
        :type image: dict
        :returns: The instance document that was edited.
        """

        update_container.signature(
            args=[str(instance['_id'])], queue='manager',
            kwargs={
                'girder_client_token': str(token['_id']),
                'image': digest
            }
        ).apply_async()

    def updateInstance(self, instance):
        """
        Updates an instance.

        :param image: The instance document to restart.
        :type image: dict
        :returns: The instance document that was edited.
        """

        instance['updated'] = datetime.datetime.utcnow()
        return self.save(instance)

    def deleteInstance(self, instance, token):
        app = getCeleryApp()
        active_queues = list(app.control.inspect().active_queues().keys())

        instanceTask = shutdown_container.signature(
            args=[str(instance['_id'])], queue='manager',
            kwargs={'girder_client_token': str(token['_id'])}
        ).apply_async()
        instanceTask.get(timeout=TASK_TIMEOUT)

        try:
            queue = 'celery@{}'.format(instance['containerInfo']['nodeId'])
            if queue in active_queues:
                volumeTask = remove_volume.signature(
                    args=[str(instance['_id'])],
                    kwargs={'girder_client_token': str(token['_id'])},
                    queue=instance['containerInfo']['nodeId']
                ).apply_async()
                volumeTask.get(timeout=TASK_TIMEOUT)
        except KeyError:
            pass

        # TODO: handle error
        self.remove(instance)

    def createInstance(self, tale, user, token, name=None, save=True,
                       spawn=True):
        if not name:
            name = tale.get('title', '')

        now = datetime.datetime.utcnow()
        instance = {
            'created': now,
            'creatorId': user['_id'],
            'iframe': tale.get('iframe', False),
            'lastActivity': now,
            'name': name,
            'status': InstanceStatus.LAUNCHING,
            'taleId': tale['_id']
        }

        self.setUserAccess(instance, user=user, level=AccessType.ADMIN)
        if save:
            instance = self.save(instance)

        if spawn:
            # Create single job
            Token().addScope(token, scope=REST_CREATE_JOB_TOKEN_SCOPE)

            buildTask = build_tale_image.signature(
                args=[str(tale['_id'])], immutable=True,
                kwargs={'girder_client_token': str(token['_id'])}
            )
            volumeTask = create_volume.si(
                str(instance['_id']),
                girder_client_token=str(token['_id'])
            )
            serviceTask = launch_container.signature(
                kwargs={'girder_client_token': str(token['_id'])},
                queue='manager'
            )

            (buildTask | volumeTask | serviceTask).apply_async()
        return instance


def _wait_for_server(url, timeout=30, wait_time=0.5):
    """Wait for a server to show up within a newly launched instance."""
    tic = time.time()
    while time.time() - tic < timeout:
        try:
            urlopen(url, timeout=1)
        except HTTPError as err:
            logger.info(
                'Booting server at [%s], getting HTTP status [%s]', url, err.code)
            time.sleep(wait_time)
        except URLError as err:
            logger.info(
                'Booting server at [%s], getting URLError due to [%s]', url, err.reason)
            time.sleep(wait_time)
        except ssl.SSLError:
            logger.info(
                'Booting server at [%s], getting SSLError', url)
            time.sleep(wait_time)
        except ConnectionError:
            logger.info(
                'Booting server at [%s], getting ConnectionError', url)
            time.sleep(wait_time)
        except Exception as ex:
            logger.info(
                'Booting server at [%s], getting "%s"', url, str(ex))
        else:
            break


def finalizeInstance(event):
    job = event.info['job']
    if job['title'] == 'Spawn Instance' and job.get('status') is not None:
        status = int(job['status'])
        instance = Instance().load(
            job['args'][0]['instanceId'], force=True)
        if status == JobStatus.SUCCESS:
            service = getCeleryApp().AsyncResult(job['celeryTaskId']).get()
            valid_keys = set(containerInfoSchema['properties'].keys())
            containerInfo = {key: service.get(key, '') for key in valid_keys}
            url = service.get('url', 'https://google.com')
            _wait_for_server(url)

            # Preserve the imageId / current digest in containerInfo
            tale = Tale().load(instance['taleId'], force=True)
            containerInfo['imageId'] = tale['imageId']
            containerInfo['digest'] = tale['imageInfo']['digest']

            instance.update({
                'url': url,
                'status': InstanceStatus.RUNNING,
                'containerInfo': containerInfo,
                'sessionId': service.get('sessionId')
            })
        elif status == JobStatus.ERROR:
            instance['status'] = InstanceStatus.ERROR
        elif status in (JobStatus.QUEUED, JobStatus.RUNNING):
            instance['status'] = InstanceStatus.LAUNCHING
        Instance().updateInstance(instance)
