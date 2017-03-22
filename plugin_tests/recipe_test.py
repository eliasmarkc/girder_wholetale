
import httmock
import json
import os
from tests import base
from .tests_helpers import \
    GOOD_REPO, GOOD_COMMIT, \
    mockOtherRequest, mockCommitRequest, mockReposRequest


def setUpModule():
    base.enabledPlugins.append('wholetale')
    base.startServer()


def tearDownModule():
    base.stopServer()


class RecipeTestCase(base.TestCase):

    def setUp(self):
        super(RecipeTestCase, self).setUp()
        users = ({
            'email': 'root@dev.null',
            'login': 'admin',
            'firstName': 'Root',
            'lastName': 'van Klompf',
            'password': 'secret'
        }, {
            'email': 'joe@dev.null',
            'login': 'joeregular',
            'firstName': 'Joe',
            'lastName': 'Regular',
            'password': 'secret'
        })
        self.admin, self.user = [self.model('user').createUser(**user)
                                 for user in users]

    def testRecipeFlow(self):
        # TODO: these should be mocked...

        with httmock.HTTMock(mockReposRequest, mockCommitRequest,
                             mockOtherRequest):
            # Verify that only authorized users can create a new recipe
            resp = self.request(
                path='/recipe', method='POST',
                params={})
            self.assertStatus(resp, 401)
            self.assertEqual(resp.json, {
                'type': 'access',
                'message': 'You must be logged in.'
            })

            # Check url validation
            resp = self.request(
                path='/recipe', method='POST', user=self.user,
                params={'url': 'blah', 'commitId': 'abcdef123'})
            self.assertStatus(resp, 400)
            self.assertEqual(resp.json, {
                'field': 'url',
                'message': 'Invalid git repository: blah.',
                'type': 'validation'
            })

            resp = self.request(
                path='/recipe', method='POST', user=self.user,
                params={
                    'url': 'https://github.com/' + os.path.dirname(GOOD_REPO),
                    'commitId': 'abcdef123'})
            self.assertStatus(resp, 400)
            self.assertEqual(resp.json, {
                'field': 'url',
                'message': ('URL does not contain repository name: '
                            'https://github.com/%s.' %
                            os.path.dirname(GOOD_REPO)),
                'type': 'validation'
            })

            # Check commit validation
            resp = self.request(
                path='/recipe', method='POST', user=self.user,
                params={'url': 'https://github.com/' + GOOD_REPO,
                        'commitId': 'abcdef123'})
            self.assertStatus(resp, 400)
            self.assertEqual(resp.json, {
                'field': 'commitId',
                'message': ('Commit Id abcdef123 does not exist in '
                            'repository whole-tale/jupyter-base'),
                'type': 'validation'
            })

            # Create a new recipe
            resp = self.request(
                path='/recipe', method='POST', user=self.user,
                params={'url': 'https://github.com/' + GOOD_REPO,
                        'commitId': GOOD_COMMIT,
                        'description': 'Text     '})
            self.assertStatusOk(resp)
            recipe = resp.json
            self.assertEqual(recipe['description'], 'Text')

            # Verify that recipe is a singleton
            resp = self.request(
                path='/recipe', method='POST', user=self.user,
                params={'url': 'https://github.com/' + GOOD_REPO,
                        'commitId': GOOD_COMMIT})
            self.assertStatus(resp, 400)
            self.assertEqual(resp.json, {
                'field': 'commitId',
                'message': 'A recipe with that url and commitId already exists.',
                'type': 'validation'
            })

            # TODO: parentage does not work currently
            # resp = self.request(
            #    path='/recipe', method='POST', user=self.user,
            #    params={'url': 'https://github.com/' + GOOD_REPO,
            #            'commitId': GOOD_CHILD})
            # self.assertStatusOk(resp)
            # child = resp.json

        # Update the recipe
        resp = self.request(
            path='/recipe/{_id}'.format(**recipe),
            method='PUT', user=self.user,
            params={'name': 'new name',
                    'description': 'new description',
                    'tags': json.dumps(['latest', 'greatest']),
                    'public': True}
        )
        self.assertStatusOk(resp)
        recipe = resp.json
        self.assertEqual(recipe['description'], 'new description')
        self.assertEqual(recipe['name'], 'new name')
        self.assertEqual(recipe['tags'], ['latest', 'greatest'])

        # test search modes
        resp = self.request(
            path='/recipe', method='GET', user=self.user,
            params={'text': 'new'})
        self.assertStatusOk(resp)
        self.assertEqual(resp.json[0]['_id'], recipe['_id'])

        # Delete the recipe
        resp = self.request(
            path='/recipe/{_id}'.format(**recipe),
            method='GET')
        self.assertStatusOk(resp)
        self.assertEqual(resp.json['_id'], str(recipe['_id']))

        resp = self.request(
            path='/recipe/{_id}'.format(**recipe),
            method='DELETE', user=self.user)
        self.assertStatus(resp, 403)

        resp = self.request(
            path='/recipe/{_id}'.format(**recipe),
            method='DELETE', user=self.admin)
        self.assertStatusOk(resp)

        resp = self.request(
            path='/recipe/{_id}'.format(**recipe),
            method='GET', user=self.user)
        self.assertStatus(resp, 400)
        self.assertEqual(resp.json, {
            'message': 'Invalid recipe id (%s).' % recipe['_id'],
            'type': 'rest'
        })

    def tearDown(self):
        self.model('user').remove(self.user)
        self.model('user').remove(self.admin)
        super(RecipeTestCase, self).tearDown()
