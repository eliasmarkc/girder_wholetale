#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import cherrypy
from urllib.parse import urlparse, urlunparse, urlencode
from girder.api import access
from girder.api.describe import Description, autoDescribeRoute
from girder.api.rest import setResponseHeader, boundHandler


@access.public
@autoDescribeRoute(
    Description('Handle a DataONE import request and bounce it to the dashboard.')
    .param('uri', 'The URI of the dataset. This cna be the landing page, pid, or doi.',
           required=True)
    .param('title', 'The Dataverse database ID of a file the external tool has '
           'been launched on.', required=False)
    .param('environment', 'The environment that should be selected.', required=False)
    .param('api', 'An optional API endpoint that should be used to find the dataset.',
           required=False)
    .param('apiToken', 'The DataONE JWT of the user importing the data, '
           'if available.', required=False)
    .notes('apiToken is currently ignored.')
)
@boundHandler()
def dataoneDataImport(self, uri, title, environment, api, apiToken):

    query = dict()
    query['uri'] = uri
    if title:
        query['name'] = title
    if environment:
        query['environment'] = environment
    if api:
        query['api'] = api

    # TODO: Make base url a plugin setting, defaulting to dashboard.<domain>
    dashboard_url = os.environ.get('DASHBOARD_URL', 'https://dashboard.wholetale.org')
    location = urlunparse(
        urlparse(dashboard_url)._replace(
            path='/compose',
            query=urlencode(query))
    )
    setResponseHeader('Location', location)
    cherrypy.response.status = 303
