import hashlib
import io
import tempfile
import xml.etree.cElementTree as ET
from urllib.request import urlopen
from shutil import copyfileobj
import uuid
import requests

from girder import logger
from girder.models.file import File
from girder.models.item import Item
from girder.api.rest import RestException
from girder.constants import \
    AccessType

from .utils import \
    check_pid, \
    get_tale_description, \
    get_file_item, \
    strip_html_tags, \
    get_directory

from .constants import \
    ExtraFileNames, \
    license_text, \
    file_descriptions

from d1_common.types import dataoneTypes
from d1_common import const as d1_const
from d1_common.resource_map import createSimpleResourceMap


def create_resource_map(resmap_pid, eml_pid, file_pids):
    """
    Creates a resource map for the package.

    :param resmap_pid: The pid od the resource map
    :param eml_pid: The pid of the science metadata
    :param file_pids: The pids for each file in the package
    :type resmap_pid: str
    :type eml_pid: str
    :type file_pids: list
    :return: The resource map for the package
    :rtype: bytes
    """

    res_map = createSimpleResourceMap(resmap_pid, eml_pid, file_pids)
    # createSimpleResourceMap returns type d1_common.resource_map.ResourceMap
    return res_map.serialize()


def create_entity(root, name, description):
    """
    Create an otherEntity section
    :param root: The parent element
    :param name: The name of the object
    :param description: The description of the object
    :type root: xml.etree.ElementTree.Element
    :type name: str
    :type description: str
    :return: An entity section
    :rtype: xml.etree.ElementTree.Element
    """
    entity = ET.SubElement(root, 'otherEntity')
    ET.SubElement(entity, 'entityName').text = name
    if description:
        ET.SubElement(entity, 'entityDescription').text = description
    return entity


def create_physical(other_entity_section, name, size):
    """
    Creates a `physical` section.
    :param other_entity_section: The super-section
    :param name: The name of the object
    :param size: The size in bytes of the object
    :type other_entity_section: xml.etree.ElementTree.Element
    :type name: str
    :type size: str
    :return: The physical section
    :rtype: xml.etree.ElementTree.Element
    """
    physical = ET.SubElement(other_entity_section, 'physical')
    ET.SubElement(physical, 'objectName').text = name
    size_element = ET.SubElement(physical, 'size')
    size_element.text = str(size)
    size_element.set('unit', 'bytes')
    return physical


def create_format(object_format, physical_section):
    """
    Creates a `dataFormat` field in the EML to describe the format
     of the object
    :param object_format: The format of the object
    :param physical_section: The etree element defining a `physical` EML section
    :type object_format: str
    :type physical_section: xml.etree.ElementTree.Element
    :return: None
    """
    data_format = ET.SubElement(physical_section, 'dataFormat')
    externally_defined = ET.SubElement(data_format, 'externallyDefinedFormat')
    ET.SubElement(externally_defined, 'formatName').text = object_format


def create_intellectual_rights(dataset_element, license_id):
    """
    :param dataset_element: The xml element that defines the `dataset`
    :param license_id: The ID of the license
    :type dataset_element: xml.etree.ElementTree.Element
    :type license_id: str
    :return: None
    """
    intellectual_rights = ET.SubElement(dataset_element, 'intellectualRights')
    section = ET.SubElement(intellectual_rights, 'section')
    para = ET.SubElement(section, 'para')
    ET.SubElement(para, 'literalLayout').text = \
        license_text.get(license_id, '')


def add_object_record(root, name, description, size, object_format):
    """
    Add a section to the EML that describes an object.
    :param name: The name of the object
    :param description: The object's description
    :param size: The size of the object
    :param object_format: The format type
    :type name: str
    :type description: str
    :type size: str
    :type object_format: str
    :return: None
    """
    entity_section = create_entity(root, name, strip_html_tags(description))
    physical_section = create_physical(entity_section,
                                       name,
                                       size)
    create_format(object_format, physical_section)
    ET.SubElement(entity_section, 'entityType').text = 'dataTable'


def set_user_name(root, firstName, lastName):
    """
    Creates a section in the EML that describes a user's name.
    :param root: The parent XML element
    :param firstName: The user's first name
    :param lastName: The user's last name
    :type root: xml.etree.ElementTree.Element
    :type firstName: str
    :type lastName: str
    :return: None
    """
    individual_name = ET.SubElement(root, 'individualName')
    ET.SubElement(individual_name, 'givenName').text = firstName
    ET.SubElement(individual_name, 'surName').text = lastName


def set_user_contact(root, user_id, email):
    """
    Creates a section that describes the contact and owner
    :param root: The parent XML element
    :param user_id: The user's ID
    :param email: The user's email
    :type root: xml.etree.ElementTree.Element
    :type user_id: str
    :type email: str
    :return: None
    """
    ET.SubElement(root, 'electronicMailAddress').text = email
    userId = ET.SubElement(root, 'userId')
    userId.text = user_id
    userId.set('directory', get_directory(user_id))


def create_minimum_eml(tale,
                       user,
                       item_ids,
                       eml_pid,
                       file_sizes,
                       license_id,
                       user_id,
                       new_dataone_objects=list()):
    """
    Creates a bare minimum EML record for a package. Note that the
    ordering of the xml elements matters.

    :param tale: The tale that is being packaged.
    :param user: The user that hit the endpoint
    :param item_ids: A list of the item ids of the objects that are going to be packaged
    :param eml_pid: The PID for the eml document. Assume that this is the package doi
    :param file_sizes: When we upload files that are not in the girder system (ie not
     files or items) we need to manually pass their size in. Use this dict to do that.
    :param license_id: The ID of the license
    :param user_id: The user's user id from the JWT
    :param new_dataone_objects: Objects that were uploaded to DataONE that don't have
    girder items/files
    :type tale: wholetale.models.tale
    :type user: girder.models.user
    :type item_ids: list
    :type eml_pid: str
    :type file_sizes: dict
    :type license_id: str
    :type user_id: str
    :type: new_dataone_objects: list
    :return: The EML as as string of bytes
    :rtype: bytes
    """

    """
    Check that we're able to assign a first, last, and email to the record.
    If we aren't throw an exception and let the user know. We'll also check that
    the user has a userID from their JWT.
    """
    lastName = user.get('lastName', None)
    firstName = user.get('firstName', None)
    email = user.get('email', None)

    if any((None for x in [lastName, firstName, email])):
        raise RestException('Unable to find your name or email address. Please ensure '
                            'you have authenticated with DataONE.')

    logger.debug('Creating EML Record')
    # Create the namespace
    ns = ET.Element('eml:eml')
    ns.set('xmlns:eml', "eml://ecoinformatics.org/eml-2.1.1")
    ns.set('xsi:schemaLocation', "eml://ecoinformatics.org/eml-2.1.1 eml.xsd")
    ns.set('xmlns:stmml', "http://www.xml-cml.org/schema/stmml-1.1")
    ns.set('xmlns:xsi', "http://www.w3.org/2001/XMLSchema-instance")
    ns.set('scope', "system")
    ns.set('system', "knb")
    ns.set('packageId', eml_pid)

    """
    Create a `dataset` field, and assign the title to
     the name of the Tale. The DataONE Quality Engine
     prefers to have titles with at least 7 words.
    """
    dataset = ET.SubElement(ns, 'dataset')
    ET.SubElement(dataset, 'title').text = str(tale.get('title', ''))

    """
    Create a `creator` section, using the information in the
     `model.user` object to provide values.
    """
    creator = ET.SubElement(dataset, 'creator')
    set_user_name(creator, firstName, lastName)
    set_user_contact(creator, user_id, email)

    # Create a `description` field, but only if the Tale has a description.
    description = get_tale_description(tale)
    if description is not str():
        abstract = ET.SubElement(dataset, 'abstract')
        ET.SubElement(abstract, 'para').text = strip_html_tags(description)

    # Add a section for the license file
    create_intellectual_rights(dataset, license_id)

    # Add a section for the contact
    contact = ET.SubElement(dataset, 'contact')
    set_user_name(contact, firstName, lastName)
    set_user_contact(contact, user_id, email)

    # Add a <otherEntity> block for each object
    for item in item_ids:
        # Create the record for the object
        add_object_record(dataset,
                          item['name'],
                          item.get('description', ''),
                          item['size'],
                          item['mimeType'])

    for new_dataone_object in new_dataone_objects:
        # Create the record for the object
        logger.debug('Adding copy EML record {}'.format(new_dataone_object))
        add_object_record(dataset,
                          new_dataone_object['name'],
                          new_dataone_object['description'],
                          new_dataone_object['size'],
                          new_dataone_object['mimeType'])

    # Add a section for the tale.yml file
    logger.debug('Adding tale.yaml to EML')
    file_sizes.get('tale_yaml')
    description = file_descriptions[ExtraFileNames.tale_config]
    name = ExtraFileNames.tale_config
    object_format = 'application/x-yaml'
    add_object_record(dataset,
                      name,
                      description,
                      file_sizes.get('tale_yaml'),
                      object_format)

    # Add a section for the license file
    if file_sizes.get('license'):
        logger.debug('Adding LICENSE to EML')
        description = file_descriptions[ExtraFileNames.license_filename]
        name = ExtraFileNames.license_filename
        object_format = 'text/plain'
        add_object_record(dataset,
                          name,
                          description,
                          file_sizes.get('license'),
                          object_format)

    # Add a section for the repository file
    if file_sizes.get('repository'):
        logger.debug('Adding repository.tar.gz to EML')
        description = file_descriptions[ExtraFileNames.environment_file]
        name = ExtraFileNames.environment_file
        object_format = 'application/tar+gzip'
        add_object_record(dataset,
                          name,
                          description,
                          file_sizes.get('repository'),
                          object_format)
    """
    Emulate the behavior of ElementTree.tostring in Python 3.6.0
     Write the contents to a stream and then return its content.
     The Python 3.4 version of ElementTree.tostring doesn't allow for
     `xml_declaration` to be set, so make a direct call to
     ElementTree.write, passing xml_declaration in.
    """
    stream = io.BytesIO()
    ET.ElementTree(ns).write(file_or_filename=stream,
                             encoding='UTF-8',
                             xml_declaration=True,
                             method='xml',
                             short_empty_elements=True)

    return stream.getvalue()


def compute_md5(file):
    """
    Takes an file handle and computes the md5 of it. This uses duck typing
    to allow for any file handle that supports .read. Note that it is left to the
    caller to close the file handle and to handle any exceptions

    :param file: An open file handle that can be read
    :return: Returns an updated md5 object. Returns None if it fails
    :rtype: md5
    """
    md5 = hashlib.md5()
    while True:
        buf = file.read(8192)
        if not buf:
            break
        md5.update(buf)
    return md5


def get_file_md5(file_object):
    """
    Computes the md5 of a file on the Girder filesystem.

    :param file_object: The file object that will be hashed
    :type file_object: girder.models.file
    :return: Returns an updated md5 object. Returns None if it fails
    :rtype: md5
    """

    assetstore = File().getAssetstoreAdapter(file_object)

    try:
        file = assetstore.open(file_object)
        md5 = compute_md5(file)
    except Exception as e:
        logger.warning('Error: {}'.format(e))
        raise RestException('Failed to download and md5 a remote file. {}'.format(e))
    finally:
        file.close()
    return md5


def create_external_object_structure(external_files, user):
    """
    Creates a JSON file that describes a file in Globus which has the following format
     {file_name : {'url': url, 'md5': md5}
     We'll want to compute the md5, so we have to save the file
     temporarily.

    :param external_files: A list of files that exist outside WholeTale
    :param user: The user publishing the tale
    :type external_files: list
    :type user: girder.mnodels.user
    :return: A dictionary that lists each remote file with its md5
    :rtype: dict
    """

    reference_file = dict()

    for item in external_files:
        """
        Get the underlying file object from the supplied item id.
        We'll need the `linkUrl` field to determine where it is pointing to.
        """
        logger.debug('Creating reference for remote files')
        file = get_file_item(item, user)
        if file is not None:
            url = file.get('linkUrl', None)
            if url is not None:
                """
                Create a temporary file object which will eventually hold the contents
                of the remote object.
                """
                with tempfile.NamedTemporaryFile() as temp_file:
                    try:
                        src = urlopen(url)
                        # Copy the response into the temporary file
                        copyfileobj(src, temp_file)

                    except requests.exceptions.HTTPError:
                        # if we fail to download the file, exit
                        raise RestException('There was a problem downloading an external file, {}'
                                            'located at {}.'.format(file['name'], url))

                    # Get the md5 of the file
                    md5 = compute_md5(temp_file)
                    digest = md5.hexdigest()

                    """
                    Create dictionary entries for the file. We key off of the file name,
                    and store the url and md5 with it.
                    """
                    url_entry = {'url': url}
                    md5_entry = {'md5': digest}
                    reference_file[file['name']] = url_entry, md5_entry

    return reference_file


def generate_system_metadata(pid,
                             format_id,
                             file_object,
                             name,
                             rights_holder,
                             is_file=False):
    """
    Generates a metadata document describing the file_object.

    :param pid: The pid that the object will have
    :param format_id: The format of the object (e.g text/csv)
    :param file_object: The object that is being described
    :param name: The name of the object being described
    :param rights_holder: The owner of this object
    :param is_file: A bool set to true if file_object is a girder file
    :type pid: str
    :type format_id: str
    :type file_object: unicode or girder.models.file
    :type name: str
    :type rights_holder: str
    :type is_file: Bool
    :return: The metadata describing file_object
    :rtype: d1_common.types.generated.dataoneTypes_v2_0.SystemMetadata
    """

    md5 = hashlib.md5()
    if is_file:
        # If it's a local file, get the md5 of it
        md5 = get_file_md5(file_object)
        size = file_object['size']
    else:
        # Check that the file_object is unicode, attempt to convert it if it's a str
        if not isinstance(file_object, bytes):
            if isinstance(file_object, str):
                file_object = file_object.encode("utf-8")
        md5.update(file_object)
        size = len(file_object)
    md5 = md5.hexdigest()

    sys_meta = populate_sys_meta(pid,
                                 format_id,
                                 size,
                                 md5,
                                 name,
                                 rights_holder)
    return sys_meta


def populate_sys_meta(pid, format_id, size, md5, name, rights_holder):
    """
    Fills out the system metadata object with the needed properties

    :param pid: The pid of the system metadata document
    :param format_id: The format of the document being described
    :param size: The size of the document that is being described
    :param md5: The md5 hash of the document being described
    :param name: The name of the file
    :param rights_holder: The owner of this object
    :type pid: str
    :type format_id: str
    :type size: int
    :type md5: str
    :type name: str
    :type rights_holder: str
    :return: The populated system metadata document
    """

    pid = check_pid(pid)
    sys_meta = dataoneTypes.systemMetadata()
    sys_meta.identifier = pid
    sys_meta.formatId = format_id
    sys_meta.size = size
    sys_meta.rightsHolder = rights_holder
    sys_meta.checksum = dataoneTypes.checksum(str(md5))
    sys_meta.checksum.algorithm = 'MD5'
    sys_meta.accessPolicy = generate_public_access_policy()
    sys_meta.fileName = name
    return sys_meta


def generate_public_access_policy():
    """
    Creates the access policy for the system metadata.
     Note that the permission is set to 'read'.

    :return: The access policy
    :rtype: d1_common.types.generated.dataoneTypes_v1.AccessPolicy
    """

    access_policy = dataoneTypes.accessPolicy()
    access_rule = dataoneTypes.AccessRule()
    access_rule.subject.append(d1_const.SUBJECT_PUBLIC)
    permission = dataoneTypes.Permission('read')
    access_rule.permission.append(permission)
    access_policy.append(access_rule)
    return access_policy


def transfer_prod_to_dev(items,
                         user,
                         user_id,
                         client,
                         previous_failure=False):
    """
    Takes an object on DataONE and transfers it to the node that client is
    interfacing with. If it fails, the failed items are sent back through
    the function as the `items` param.
    :param items: A list of linkFile items that are going to be transferred
    :param user: The user doing the transfer
    :param user_id: The user's userId from their jwt
    :param client: The dataone client interfacing the network
    :param previous_failure: Set if this function has failed in recursion
    :type : list
    :type : girder.models.user
    :type : str
    :type : MemberNodeClient_2_0
    :type previous_failure: bool
    :return:
    """

    new_objects = list()
    failed_items = list()
    for itemId in items:
        """
        Get the underlying file object from the supplied item id.
        We'll need the `linkUrl` field to determine where it is pointing to.
        """
        file = get_file_item(itemId, user)
        if file is None:
            # We'll want to exit if we fail to load one of the user's files
            raise RestException('Failed to locate file {}'.format(itemId))

        url = file.get('linkUrl')
        if url is not None:
            """
            Create a temporary file object which will eventually hold the contents
            of the remote object.
            """
            with tempfile.NamedTemporaryFile(mode='rb+') as temp_file:
                try:
                    src = requests.get(url)
                    src.raise_for_status()
                    temp_file.write(src.content)
                except requests.exceptions.HTTPError:
                    # if we fail to download the file, exit
                    raise RestException('Failed to download file {}, located at {}'
                                        .format(file['name'], url))

                # Create a pid for the new file
                pid = str(uuid.uuid4())
                """
                The file going to be read while creating the metadata, so
                skip to the beginning.
                """
                temp_file.seek(0)
                meta = generate_system_metadata(pid=pid,
                                                format_id=file['mimeType'],
                                                file_object=temp_file.read(),
                                                name=file['name'],
                                                rights_holder=user_id)

                """
                  Create a new object that holds properties about the file so that they can be
                  used in the EML,
                  """
                new_object = dict()
                new_object['pid'] = pid
                new_object['name'] = file['name']
                new_object['size'] = file['size']
                new_object['mimeType'] = file['mimeType']

                item = Item().load(itemId, level=AccessType.READ, user=user)
                if item is None:
                    """
                    If the item failed to load, we'll be unable to get the file's
                    description. This doesn't warrant stopping publication, but the
                    user should know.
                    """
                    logger.warning('Failed to load item {} during upload to DataONE'.format(itemId))
                else:
                    new_object['description'] = item['description']

                """
                Make sure we skip to the beginning of the file before streaming it
                """
                temp_file.seek(0)
                logger.debug('Uploading a downlaoded DataONE object to the current network')
                try:
                    logger.debug('Transferring item {} ID {}'.format(file['name'], itemId))
                    client.create(pid, io.BytesIO(temp_file.read()), meta)
                    new_objects.append(new_object)
                    logger.debug('Finished transferring object')
                except Exception as e:
                    """
                    If the upload failed, we'll want to try it again later, so save the id
                    """
                    logger.warning('Error uploading object with ID {}. Error: {}'
                                   .format(itemId, e))
                    failed_items.append(itemId)
                    pass

    retry_objects = list()
    if failed_items:
        logger.debug('{} items failed to upload to DataONE.'.format(len(failed_items)))
        if previous_failure:
            # If we already failed once, quit and let the user know there was an error
            logger.warning('Failed to upload to Dataone. Terminating.')
            raise RestException('There was an error while uploading your files to the server.')
        retry_objects = transfer_prod_to_dev(failed_items, user, user_id, client, True)

    logger.debug('Finished uploading objects to dev {}'.format(new_objects))
    if retry_objects:
        return new_objects+retry_objects
    return new_objects