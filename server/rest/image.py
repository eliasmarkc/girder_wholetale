#!/usr/bin/env python
# -*- coding: utf-8 -*-
from girder.api import access
from girder.api.docs import addModel
from girder.api.describe import Description, autoDescribeRoute
from girder.api.rest import Resource, filtermodel, RestException
from girder.constants import AccessType, SortDir, TokenScope
from ..constants import containerConfigSchema, tagsSchema


imageModel = {
    "description": "Object representing a WT Image.",
    "required": [
        "_id",
        "fullName",
        "status",
        "imageId",
        "digest",
        "tags",
        "parentId"
    ],
    "properties": {
        "_id": {
            "type": "string",
            "description": "internal unique identifier"
        },
        "name": {
            "type": "string",
            "description": "A user-friendly name"
        },
        "fullName": {
            "type": "string",
            "description": ("An image name following docker format: "
                            "namespace/repository(@digest)"),
        },
        "description": {
            "type": "string"
        },
        "config": {
            "$ref": "#/definitions/containerConfig"
        },
        "imageId": {
            "type": "string",
            "description": "A image used to build the image."
        },
        "digest": {
            "type": "string",
            "description": ("Checksum of a successfully built image "
                            "that can be used to pull a specific version "
                            " of the image."),
        },
        "tags": {
            "type": "array",
            "items": {
                "type": "string"
            },
            "description": "A human readable identification of the Recipe.",
            "default": [
                "latest"
            ]
        },
        "parentId": {
            "type": "string",
            "description": "ID of a previous version of the Image"
        },
        "status": {
            "type": "string",
            "default": "unavailable",
            "description": "Status of the image.",
            "enum": [
                "invalid",
                "unavailable",
                "building",
                "available"
            ]
        },
        "public": {
            "type": "boolean",
            "default": True,
            "description": "If set to true the image can be accessed by anyone"
        },
        "created": {
            "type": "string",
            "format": "date-time",
            "description": "The time when the image was created."
        },
        "creatorId": {
            "type": "string",
            "description": "A unique identifier of the user that created the image."
        },
        "updated": {
            "type": "string",
            "format": "date-time",
            "description": "The last time when the image was modified."
        }
    },
    'example': {
        '_accessLevel': 2,
        '_id': '5873dcdbaec030000144d233',
        '_modelType': 'image',
        'fullName': 'Xarthisius/wt_image',
        'creatorId': '18312dcdbaec030000144d233',
        'created': '2017-01-09T18:56:27.262000+00:00',
        'description': 'My fancy image',
        'digest': '123456',
        'parentId': 'null',
        'public': True,
        'tags': ['latest', 'py3'],
        'status': 'building',
        'updated': '2017-01-10T16:15:17.313000+00:00',
    },
}
addModel('image', imageModel, resources='image')


class Image(Resource):

    def __init__(self):
        super(Image, self).__init__()
        self.resourceName = 'image'

        self.route('GET', (), self.listImages)
        self.route('POST', (), self.createImage)
        self.route('GET', (':id',), self.getImage)
        self.route('PUT', (':id',), self.updateImage)
        # self.route('DELETE', (':id',), self.deleteImage)
        self.route('PUT', (':id', 'build'), self.buildImage)
        self.route('PUT', (':id', 'check'), self.checkImage)
        self.route('POST', (':id', 'copy'), self.copyImage)

    @access.public
    @filtermodel(model='image', plugin='ythub')
    @autoDescribeRoute(
        Description(('Returns all images from the system '
                     'that user has access to'))
        .responseClass('image', array=True)
        .param('parentId', "The ID of the image's parent.", required=False)
        .param('text', 'Perform a full text search for image with a matching '
               'name or description.', required=False)
        .param('tag', 'Search all images with a given tag.', required=False)
        .pagingParams(defaultSort='lowerName',
                      defaultSortDir=SortDir.DESCENDING)
    )
    def listImages(self, parentId, text, tag, limit, offset, sort, params):
        user = self.getCurrentUser()

        if parentId:
            parent = self.model('image', 'ythub').load(
                parentId, user=user, level=AccessType.READ, exc=True)

            filters = {}
            if text:
                filters['$text'] = {
                    '$search': text
                }
            if tag:
                print('Do filtering by tag when I figure it out')

            return list(self.model('image', 'ythub').childImages(
                parent=parent, user=user,
                offset=offset, limit=limit, sort=sort, filters=filters))
        elif text:
            return list(self.model('image', 'ythub').textSearch(
                text, user=user, limit=limit, offset=offset, sort=sort))
        elif tag:
            raise RestException('Can filter by tag. yet...')
        else:
            raise RestException('Invalid search mode.')

    @access.public(scope=TokenScope.DATA_READ)
    @filtermodel(model='image', plugin='ythub')
    @autoDescribeRoute(
        Description('Get a image by ID.')
        .modelParam('id', model='image', plugin='ythub', level=AccessType.READ)
        .responseClass('image')
        .errorResponse('ID was invalid.')
        .errorResponse('Read access was denied for the image.', 403)
    )
    def getImage(self, image, params):
        return image

    @access.user
    @autoDescribeRoute(
        Description('Update an existing image.')
        .modelParam('id', model='image', plugin='ythub', level=AccessType.WRITE,
                    description='The ID of the image.')
        .param('name', 'A name of the image.', required=False)
        .param('description', 'A description of the image.',
               required=False)
        .param('public', 'Whether the image should be publicly visible.'
               ' Defaults to True.', dataType='boolean', required=False,
               default=True)
        .jsonParam('tags', 'A human readable labels for the image.',
                   required=False, schema=tagsSchema)
        .responseClass('image')
        .errorResponse('ID was invalid.')
        .errorResponse('Read/write access was denied for the image.', 403)
        .errorResponse('Tag already exists.', 409)
    )
    def updateImage(self, image, name, description, public, tags, params):
        if name is not None:
            image['name'] = name
        if description is not None:
            image['description'] = description
        if tags is not None:
            image['tags'] = tags
        # TODO: tags magic
        self.model('image', 'ythub').setPublic(image, public)
        return self.model('image', 'ythub').updateImage(image)

    @access.admin
    @autoDescribeRoute(
        Description('Delete an existing image.')
        .modelParam('id', model='image', plugin='ythub', level=AccessType.WRITE,
                    description='The ID of the image.')
        .errorResponse('ID was invalid.')
        .errorResponse('Admin access was denied for the image.', 403)
    )
    def deleteImage(self, image, params):
        self.model('image', 'ythub').remove(image)

    @access.user
    @filtermodel(model='image', plugin='ythub')
    @autoDescribeRoute(
        Description('Create a new image.')
        .param('recipeId', 'The ID of a recipe used to build the image',
               dataType='string', required=True)
        .param('fullName', 'An image name conforming to docker standard',
               dataType='string', required=True)
        .param('name', 'A name of the image.', required=False)
        .param('description', 'A description of the image.',
               required=False)
        .param('public', 'Whether the image should be publicly visible.'
               ' Defaults to True.', dataType='boolean', required=False)
        .jsonParam('tags', 'A human readable labels for the image.',
                   required=False, schema=tagsSchema)
        .jsonParam('config', 'Default image runtime configuration',
                   required=False, schema=containerConfigSchema, paramType='body')
        .responseClass('image')
        .errorResponse('Query parameter was invalid')
    )
    def createImage(self, recipeId, fullName, name, description, public, tags,
                    config, params):
        user = self.getCurrentUser()
        recipe = self.model('recipe', 'ythub').load(
            recipeId, user=user, level=AccessType.READ, exc=True)
        return self.model('image', 'ythub').createImage(
            recipe, fullName, name=name, tags=tags, creator=user,
            save=True, parent=None, description=description, public=public,
            config=config)

    @access.admin
    @autoDescribeRoute(
        Description('Build an existing image')
        .modelParam('id', model='image', plugin='ythub', level=AccessType.WRITE,
                    description='The ID of the image.')
        .errorResponse('ID was invalid.')
        .errorResponse('Admin access was denied for the image.', 403)
    )
    def buildImage(self, image, params):
        return self.model('image', 'ythub').buildImage(image)

    @access.admin
    @autoDescribeRoute(
        Description('Update/verify the status of the image')
        .modelParam('id', model='image', plugin='ythub', level=AccessType.WRITE,
                    description='The ID of the image.')
        .errorResponse('ID was invalid.')
        .errorResponse('Admin access was denied for the image.', 403)
    )
    def checkImage(self, image, params):
        return self.model('image', 'ythub').checkImage(image)

    @access.user
    @autoDescribeRoute(
        Description('Create a copy of an image using an updated recipe')
        .notes('Create a copy of an image preserving original fullName. '
               'Operation will only succeed if the new recipe is '
               'a descendant of the recipe used by the original image.')
        .modelParam('id', model='image', plugin='ythub', level=AccessType.READ,
                    description='The ID of the image.')
        .param('recipeId', 'The ID of the new recipe', required=True)
    )
    def copyImage(self, image, recipeId, params):
        user = self.getCurrentUser()
        recipe = self.model('recipe', 'ythub').load(
            recipeId, user=user, level=AccessType.READ, exc=True)
        return self.model('image', 'ythub').copyImage(image, recipe, creator=user)
