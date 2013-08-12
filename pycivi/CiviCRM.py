import requests
import logging
import sys
import json
import entity_type as etype
import time
import threading
import os
import traceback

from CiviEntity import *

class CiviAPIException(Exception):
	pass

class CiviCRM:

	def __init__(self, url, site_key, user_key, logfile=None):
		# init some attributes
		self.url = url
		self.site_key = site_key
		self.user_key = user_key

		self.lookup_cache = dict()
		self.lookup_cache_lock = threading.Condition()

		# set up logging
		self.logger_format = u"%(level)s;%(type)s;%(entity_type)s;%(first_id)s;%(second_id)s;%(duration)sms;%(thread_id)s;%(text)s"
		self._logger = logging.getLogger('pycivi')
		self._logger.setLevel(logging.DEBUG)

		# add the console logger
		logger1 = logging.StreamHandler()
		logger1.setLevel(logging.INFO)
		class MessageOnly(logging.Formatter):
			def format(self, record):
				return logging.Formatter.format(self, record).split(';')[-1]
		logger1.setFormatter(MessageOnly())
		self._logger.addHandler(logger1)

		# add the file logger
		if logfile:
			if not os.path.exists(logfile):
				log = open(logfile, 'w')
				log.write("timestamp;level;module;entity_type;primary_id;secondary_id;execution_time;thread;message\n")
				log.flush()
				log.close()

			logger2 = logging.FileHandler(logfile, mode='a')
			logger2.setLevel(logging.DEBUG)
			logger2.setFormatter(logging.Formatter(u'%(asctime)s;%(message)s'))
			self._logger.addHandler(logger2)
		
		# some more internal attributes
		self.debug = False
		self.api_version = 3
		self._api_calls = 0
		self._api_calls_time = 0.0

		# set rest url
		if self.url.endswith('extern/rest.php'):
			# in this case it's fine, this is the rest URL
			self.rest_url = self.url
		else:
			if self.url.endswith('/civicrm'):
				self.rest_url = self.url[:-8] + '/sites/all/modules/civicrm/extern/rest.php'
			else:
				self.rest_url = self.url + '/sites/all/modules/civicrm/extern/rest.php'

	def _getLevelString(self, level):
		"""
		give a textual representation of the log level
		"""
		if level==logging.DEBUG:
			return 'DEBUG'
		elif level==logging.INFO:
			return 'INFO'
		elif level==logging.WARN:
			return 'WARN'
		elif level==logging.ERROR:
			return 'ERROR'
		elif level==logging.FATAL:
			return 'FATAL'
		else:
			return 'UNKNOWN'

	def log(self, message, level=logging.INFO, type='Unknown', command='Unknown', entity_type='', first_id='', second_id='', duration='0'):
		"""
		formally log information.
		"""
		try:
			duration = str(int(duration * 1000))
		except:
			duration = '0'

		self._logger.log(level, self.logger_format,  { 	'type': type,
														'level': self._getLevelString(level),
														'command': command,
														'entity_type': entity_type,
														'first_id': first_id,
														'second_id': second_id,
														'duration': duration,
														'thread_id': threading.currentThread().name,
														'text': message,
													})

	def logException(self, message="An exception occurred: ", level=logging.ERROR, type='Unknown', command='Unknown', entity_type='', first_id='', second_id='', duration='0'):
		"""
		log current exception (in except: block)
		"""
		exception_text = ' >> ' + traceback.format_exc() + ' <<'
		exception_text = exception_text.replace('\x0A', '  ||')
		self.log(message + exception_text, level, type, command, entity_type, first_id, second_id, duration)


	def performAPICall(self, params=dict()):
		timestamp = time.time()
		params['api_key'] = self.user_key
		params['key'] = self.site_key
		params['sequential'] = 1
		params['json'] = 1
		params['version'] = self.api_version
		if self.debug:
			params['debug'] = 1

		if params['action'] in ['create', 'delete']:
			reply = requests.post(self.rest_url, params=params, verify=False)
		else:
			reply = requests.get(self.rest_url, params=params, verify=False)

		self.log("API call completed - status: %d, url: '%s'" % (reply.status_code, reply.url), 
			logging.DEBUG, 'API', params.get('action', "NO ACTION SET"), params.get('entity', "NO ENTITY SET!"), params.get('id', ''), params.get('external_identifier', ''), time.time()-timestamp)

		if reply.status_code != 200:
			raise CiviAPIException("HTML response code %d received, please check URL" % reply.status_code)

		result = json.loads(reply.text)
				
		# do some logging
		runtime = time.time()-timestamp
		self._api_calls += 1
		self._api_calls_time += runtime

		if result.has_key('undefined_fields'):
			fields = result['undefined_fields']
			if fields:
				self.log("API call: Undefined fields reported: %s" % str(fields), 
					logging.DEBUG, 'API', params['action'], params['entity'], params.get('id', ''), params.get('external_identifier', ''), time.time()-timestamp)

		if result['is_error']:
			self.log("API call error: '%s'" % result['error_message'], 
				logging.ERROR, 'API', params['action'], params['entity'], params.get('id', ''), params.get('external_identifier', ''), time.time()-timestamp)
			raise CiviAPIException(result['error_message'])
		else:
			return result


	def probe(self):
		# check by calling get contact
		try:
			self.performAPICall({'entity':'Contact', 'action':'get', 'option.limit':1})
			return True
		except:
			return False


	def load(self, entity_type, entity_id):
		result = self.performAPICall({'entity':entity_type, 'action':'get', 'id':entity_id})
		if result['count']:
			return self._createEntity(entity_type, result['values'][0])
		else:
			return None

	

	def getEntity(self, entity_type, attributes, primary_attributes=['id','external_identifier']):
		timestamp = time.time()

		query = dict()
		first_key = None
		for key in primary_attributes: 
			if attributes.has_key(key):
				query[key] = attributes[key]
				if first_key==None:
					first_key = attributes[key]
		if not len(query) > 0:
			self.log("No primary key provided with contact '%s'." % str(attributes),
				logging.DEBUG, 'pycivi', 'get', entity_type, first_key, None, time.time()-timestamp)
			return 0

		query['entity'] = entity_type
		query['action'] = 'get'
		result = self.performAPICall(query)
		if result['is_error']:
			raise CiviAPIException(result['error_message'])
		if result['count']>1:
			self.log("Query result not unique, please provide a unique query for 'getEntity'.",
				logging.WARN, 'pycivi', 'get', entity_type, first_key, None, time.time()-timestamp)
			raise CiviAPIException("Query result not unique, please provide a unique query for 'getEntity'.")
		elif result['count']==1:
			entity = self._createEntity(entity_type, result['values'][0])
			self.log("Entity found: %s" % unicode(str(entity), 'utf8'),
				logging.DEBUG, 'pycivi', 'get', entity_type, first_key, None, time.time()-timestamp)
			return entity
		else:
			self.log("Entity not found.",
				logging.DEBUG, 'pycivi', 'get', entity_type, first_key, None, time.time()-timestamp)
			return None


	def createOrUpdate(self, entity_type, attributes, update_type='update', primary_attributes=[u'id', u'external_identifier']):
		query = dict()
		for key in primary_attributes: 
			if attributes.has_key(key):
				query[key] = attributes[key]
		query['entity'] = entity_type
		query['action'] = 'get'
		result = self.performAPICall(query)
		if result['count']>1:
			raise CiviAPIException("Query result not unique, please provide a unique query for 'getOrCreate'.")
		else:
			if result['count']==1:
				entity = self._createEntity(entity_type, result['values'][0])
				if update_type=='update':
					entity.update(attributes, True)
				elif update_type=='fill':
					entity.fill(attributes, True)
				elif update_type=='replace':
					entity.replace(attributes, True)
				else:
					raise CiviAPIException("Bad update_type '%s' selected. Must be 'update', 'fill' or 'replace'." % update_type)
				return entity
			else:
				query.update(attributes)
				query['action'] = 'create'
				result = self.performAPICall(query)
				if result['is_error']:
					raise CiviAPIException(result['error_message'])
				return self._createEntity(entity_type, result['values'][0])


	###########################################################################
	#                            Lookup methods                               #
	###########################################################################


	def getContactID(self, attributes, primary_attributes=['external_identifier'], search_deleted=True):
		timestamp = time.time()
		if attributes.has_key('id'):
			return attributes['id']
		elif attributes.has_key('contact_id'):
			return attributes['contact_id']
		
		query = dict()
		first_key = None
		for key in primary_attributes: 
			if attributes.has_key(key):
				query[key] = attributes[key]
				if first_key==None:
					first_key = attributes[key]
		if not len(query) > 0:
			self.log("No primary key provided with contact '%s'." % str(attributes),
				logging.DEBUG, 'pycivi', 'get', 'Contact', first_key, None, time.time()-timestamp)
			return 0

		query['entity'] = 'Contact'
		query['action'] = 'get'
		query['return'] = 'contact_id'

		result = self.performAPICall(query)
		if result['count']>1:
			self.log("Query result not unique, please provide a unique query for 'getOrCreate'.",
				logging.WARN, 'pycivi', 'get', 'Contact', first_key, None, time.time()-timestamp)
			raise CiviAPIException("Query result not unique, please provide a unique query for 'getOrCreate'.")
		elif result['count']==1:
			contact_id = result['values'][0]['contact_id']
			self.log("Contact ID resolved.",
				logging.DEBUG, 'pycivi', 'get', 'Contact', first_key, None, time.time()-timestamp)
			return contact_id
		else:
			if search_deleted and not int(attributes.get('is_deleted', '0'))==1:
				# NOT found, but we haven't looked into the deleted contacts
				#print "NOT FOUND. LOOKING IN DELTED."
				new_attributes = dict(attributes)
				new_primary_attributes = list(primary_attributes)
				new_attributes['is_deleted'] = '1'
				new_primary_attributes += ['is_deleted']
				return self.getContactID(new_attributes, new_primary_attributes, search_deleted)
			#print "STILL NOT FOUND!"
			self.log("Contact not found.",
				logging.DEBUG, 'pycivi', 'get', 'Contact', first_key, None, time.time()-timestamp)
			return 0


	def getCampaignID(self, attribute_value, attribute_key='title'):
		"""
		Get the ID for a given campaign

		Results will be cached
		"""
		timestamp = time.time()
		if self.lookup_cache.has_key('campaign') and self.lookup_cache['campaign'].has_key(attribute_key) and self.lookup_cache['campaign'][attribute_key].has_key(attribute_value):
			return self.lookup_cache['campaign'][attribute_key][attribute_value]

		query = dict()
		query['entity'] = 'Campaign'
		query['action'] = 'get'
		query[attribute_key] = attribute_value
		result = self.performAPICall(query)
		if result['count']>1:
			campaign_id = 0
			self.log(u"More than one campaign found with %s '%s'!" % (attribute_key, attribute_value),
				logging.WARN, 'pycivi', 'getCampaignID', 'Campaign', None, None, time.time()-timestamp)
		elif result['count']==0:
			campaign_id = 0
			self.log(u"No campaign found with %s '%s'!" % (attribute_key, attribute_value),
				logging.DEBUG, 'pycivi', 'getCampaignID', 'Campaign', None, None, time.time()-timestamp)
		else:
			campaign_id = result['values'][0]['id']
			self.log(u"Campaign with %s '%s' resolved to ID %s!" % (attribute_key, attribute_value, campaign_id),
				logging.DEBUG, 'pycivi', 'getCampaignID', 'Campaign', None, None, time.time()-timestamp)

		# store value
		self.lookup_cache_lock.acquire()
		if not self.lookup_cache.has_key('campaign'):
			self.lookup_cache['campaign'] = dict()
		if not self.lookup_cache['campaign'].has_key(attribute_key):
			self.lookup_cache['campaign'][attribute_key] = dict()
		self.lookup_cache['campaign'][attribute_key][attribute_value] = campaign_id
		self.lookup_cache_lock.notifyAll()
		self.lookup_cache_lock.release()

		return campaign_id


	def getCustomFieldID(self, field_name, entity_type='Contact'):
		"""
		Get the ID for a given custom field
		"""
		timestamp = time.time()
		if self.lookup_cache.has_key('custom_field') and self.lookup_cache['custom_field'].has_key(field_name):
			return self.lookup_cache['custom_field'][field_name]

		query = dict()
		query['entity'] = 'CustomField'
		query['action'] = 'get'
		query['label'] = field_name

		result = self.performAPICall(query)
		if result['count']>1:
			field_id = 0
			self.log(u"More than one custom field found with name '%s'!" % field_name,
				logging.WARN, 'API', 'get', 'CustomField', None, None, time.time()-timestamp)
		elif result['count']==0:
			field_id = 0
			self.log(u"Custom field '%s' does not exist." % field_name,
				logging.DEBUG, 'API', 'get', 'CustomField', None, None, time.time()-timestamp)
		else:
			field_id = result['values'][0]['id']
			self.log(u"Custom field '%s' resolved to ID %s" % (field_name, field_id),
				logging.DEBUG, 'API', 'get', 'CustomField', field_id, None, time.time()-timestamp)

		# store value
		self.lookup_cache_lock.acquire()
		if not self.lookup_cache.has_key('custom_field'):
			self.lookup_cache['custom_field'] = dict()
		self.lookup_cache['custom_field'][field_name] = field_id
		self.lookup_cache_lock.notifyAll()
		self.lookup_cache_lock.release()

		return field_id


	def setCustomFieldID(self, entity_id, field_name, value):
		"""
		Get the ID for a given custom field
		"""
		timestamp = time.time()
		field_id = self.getCustomFieldID(field_name)
		if not field_id:
			self.log(u"Custom field '%s' does not exist." % field_name,
				logging.WARN, 'API', 'get', 'CustomField', None, None, time.time()-timestamp)
			return

		query = dict()
		query['entity'] = 'CustomValue'
		query['action'] = 'get'
		query['entity_id'] = entity_id
		query['label'] = field_name

		result = self.performAPICall(query)
		if result['is_error']:
			raise CiviAPIException(result['error_message'])
		if result['count']>1:
			field_id = 0
			self.log(u"More than one custom field found with name '%s'!" % field_name,
				logging.WARN, 'API', 'get', 'CustomField', None, None, time.time()-timestamp)
		elif result['count']==0:
			field_id = 0
			self.log(u"Custom field '%s' does not exist." % field_name,
				logging.DEBUG, 'API', 'get', 'CustomField', None, None, time.time()-timestamp)
		else:
			field_id = result['values'][0]['id']
			self.log(u"Custom field '%s' resolved to ID %s" % (field_name, field_id),
				logging.DEBUG, 'API', 'get', 'CustomField', field_id, None, time.time()-timestamp)

		# store value
		self.lookup_cache_lock.acquire()
		if not self.lookup_cache.has_key('custom_field'):
			self.lookup_cache['custom_field'] = dict()
		self.lookup_cache['custom_field'][field_name] = field_id
		self.lookup_cache_lock.notifyAll()
		self.lookup_cache_lock.release()

		return field_id


	def getOptionGroupID(self, group_name):
		"""
		Get the ID for a given option group
		"""
		timestamp = time.time()
		if self.lookup_cache.has_key('option_group') and self.lookup_cache['option_group'].has_key(group_name):
			return self.lookup_cache['option_group'][group_name]

		query = dict()
		query['entity'] = 'OptionGroup'
		query['action'] = 'get'
		query['name'] = group_name
		result = self.performAPICall(query)
		if result['is_error']:
			raise CiviAPIException(result['error_message'])
		if result['count']>1:
			group_id = 0
			self.log("More than one group found with name '%s'!" % group_name,
				logging.WARN, 'API', 'get', 'OptionGroup', None, None, time.time()-timestamp)
		elif result['count']==0:
			group_id = 0
			self.log("Group '%s' does not exist." % group_name,
				logging.DEBUG, 'API', 'get', 'OptionGroup', group_id, None, time.time()-timestamp)
		else:
			group_id = result['values'][0]['id']
			self.log("Group '%s' resolved to ID %s" % (group_name, group_id),
				logging.DEBUG, 'API', 'get', 'OptionGroup', group_id, None, time.time()-timestamp)

		# store value
		self.lookup_cache_lock.acquire()
		if not self.lookup_cache.has_key('option_group'):
			self.lookup_cache['option_group'] = dict()
		self.lookup_cache['option_group'][group_name] = group_id
		self.lookup_cache_lock.notifyAll()
		self.lookup_cache_lock.release()

		return group_id

	
	def getOptionValueID(self, option_group_id, name):
		"""
		Get the ID for a given option value
		"""
		timestamp = time.time()
		if self.lookup_cache.has_key('option_value') and self.lookup_cache['option_value'].has_key(option_group_id) and self.lookup_cache['option_value'][option_group_id].has_key(name):
			return self.lookup_cache['option_value'][option_group_id][name]

		query = dict()
		query['entity'] = 'OptionValue'
		query['action'] = 'get'
		query['name'] = name
		query['option_group_id'] = option_group_id
		result = self.performAPICall(query)
		if result['is_error']:
			raise CiviAPIException(result['error_message'])
		if result['count']>1:
			value_id = 0
			self.log("More than one value found with name '%s'!" % name,
				logging.WARN, 'API', 'get', 'OptionValue', None, None, time.time()-timestamp)
		elif result['count']==0:
			value_id = 0
			self.log("Value '%s' does not exist." % name,
				logging.DEBUG, 'API', 'get', 'OptionValue', value_id, None, time.time()-timestamp)
		else:
			value_id = result['values'][0]['value']
			self.log("Value '%s' resolved to ID %s" % (name, value_id),
				logging.DEBUG, 'API', 'get', 'OptionValue', value_id, None, time.time()-timestamp)

		# store value
		self.lookup_cache_lock.acquire()
		if not self.lookup_cache.has_key('option_value'):
			self.lookup_cache['option_value'] = dict()
		if not self.lookup_cache['option_value'].has_key(option_group_id):
			self.lookup_cache['option_value'][option_group_id] = dict()			
		self.lookup_cache['option_value'][option_group_id][name] = value_id
		self.lookup_cache_lock.notifyAll()
		self.lookup_cache_lock.release()

		return value_id


	def setOptionValue(self, option_group_id, name, attributes=dict()):
		"""
		Set or update the value for the given option group
		"""
		timestamp = time.time()
		query = dict(attributes)
		query['action'] = 'create'
		query['entity'] = 'OptionValue'
		query['option_group_id'] = option_group_id
		query['name'] = name
		result = self.performAPICall(query)
		if result['is_error']:
			raise CiviAPIException(result['error_message'])
		
		# store value
		value_id = result['values'][0]['value']
		self.lookup_cache_lock.acquire()
		if not self.lookup_cache.has_key('option_value'):
			self.lookup_cache['option_value'] = dict()
		if not self.lookup_cache['option_value'].has_key(option_group_id):
			self.lookup_cache['option_value'][option_group_id] = dict()			
		self.lookup_cache['option_value'][option_group_id][name] = value_id
		self.lookup_cache_lock.notifyAll()
		self.lookup_cache_lock.release()

		return value_id


	def getLocationTypeID(self, location_name):
		# first: look up in cache
		if self.lookup_cache.has_key('location_type2id') and self.lookup_cache['location_type2id'].has_key(location_name):
			return self.lookup_cache['location_type2id'][location_name]

		timestamp = time.time()
		query = { 	'action': 'get',
					'entity': 'LocationType',
					'name': location_name }
		result = self.performAPICall(query)
		if result['count']>1:
			self.log("Query result not unique, please provide a unique query for 'getOrCreate'.",
				logging.WARN, 'API', 'get', 'LocationType', None, None, time.time()-timestamp)
			raise CiviAPIException("Query result not unique, please provide a unique query for 'getOrCreate'.")
		elif result['count']==1:
			location_id = result['values'][0]['id']
			self.log("Location type '%s' resolved to id %s." % (location_name, location_id),
				logging.DEBUG, 'API', 'get', 'LocationType', location_id, None, time.time()-timestamp)
		else:
			location_id = 0
			self.log("Location type '%s' resolved to id %s." % (location_name, location_id),
				logging.ERROR, 'API', 'get', 'LocationType', location_id, None, time.time()-timestamp)
		self.lookup_cache_lock.acquire()
		if not self.lookup_cache.has_key('location_type2id'):
			self.lookup_cache['location_type2id'] = dict()
		self.lookup_cache['location_type2id'][location_name] = location_id
		self.lookup_cache_lock.notifyAll()
		self.lookup_cache_lock.release()
		return location_id


	def getMembershipStatusID(self, membership_status_name):
		# first: look up in cache
		if self.lookup_cache.has_key('membership_status2id') and self.lookup_cache['membership_status2id'].has_key(membership_status_name):
			return self.lookup_cache['membership_status2id'][membership_status_name]

		timestamp = time.time()
		query = { 	'action': 'get',
					'entity': 'MembershipStatus',
					'name': membership_status_name }
		result = self.performAPICall(query)
		if result['count']>1:
			self.log("Non-uniqe membership status name '%s'" % membership_status_name,
				logging.WARN, 'API', 'get', 'MembershipStatus', None, None, time.time()-timestamp)
			raise CiviAPIException("Non-uniqe membership status name '%s'" % membership_status_name)
		elif result['count']==1:
			status_id = result['values'][0]['id']
			self.log("Membership status '%s' resolved to id %s." % (membership_status_name, status_id),
				logging.DEBUG, 'API', 'get', 'MembershipStatus', status_id, None, time.time()-timestamp)
		else:
			status_id = 0
			self.log("Membership status '%s' could NOT be resolved",
				logging.DEBUG, 'API', 'get', 'MembershipStatus', None, None, time.time()-timestamp)

		self.lookup_cache_lock.acquire()
		if not self.lookup_cache.has_key('membership_status2id'):
			self.lookup_cache['membership_status2id'] = dict()
		self.lookup_cache['membership_status2id'][membership_status_name] = status_id
		self.lookup_cache_lock.notifyAll()
		self.lookup_cache_lock.release()
		return status_id



	def getEmail(self, contact_id, location_type_id):
		timestamp = time.time()
		query = dict()
		query['action']			 	= 'get'
		query['entity'] 			= 'Email'
		query['contact_id'] 		= contact_id
		query['location_type_id'] 	= location_type_id
		result = self.performAPICall(query)
		if result['is_error']:
			raise CiviAPIException(result['error_message'])
		if result['count']>1:
			self.log("Contact %s has more then one %s email address. Delivering first!" % (query.get('contact_id', 'n/a'), query.get('location_type', 'n/a')),
				logging.WARN, 'pycivi', 'get', 'Email', query.get('contact_id', None), None, time.time()-timestamp)
		elif result['count']==0:
			return None
		return self._createEntity('Email', result['values'][0])


	def getEmails(self, contact_id, location_type_id=None):
		timestamp = time.time()
		query = dict()
		query['action']			 	= 'get'
		query['entity'] 			= 'Email'
		query['contact_id'] 		= contact_id
		if location_type_id:
			query['location_type_id'] 	= location_type_id

		result = self.performAPICall(query)
		if result['is_error']:
			raise CiviAPIException(result['error_message'])

		emails = list()
		for email_data in result['values']:
			emails.append(self._createEntity('Email', email_data))

		self.log("Found %d email addresses (type %s) for contact %s." % (len(emails), location_type_id, query.get('contact_id', 'n/a')),
			logging.DEBUG, 'pycivi', 'get', 'Email', query.get('contact_id', None), None, time.time()-timestamp)

		return emails


	def createEmail(self, contact_id, location_type_id, email):
		timestamp = time.time()
		query = dict()
		query['action'] 			= 'create'
		query['entity'] 			= 'Email'
		query['location_type_id'] 	= location_type_id
		query['contact_id'] 		= contact_id
		query['email']  			= email
		result = self.performAPICall(query)
		if result['is_error']:
			raise CiviAPIException(result['error_message'])
		return self._createEntity('Email', result['values'][0])


	def getPhoneNumber(self, data):
		timestamp = time.time()
		query = dict()
		query['action'] = 'get'
		query['entity'] = 'Phone'
		query['contact_id'] = data['contact_id']
		query['phone_type'] = data['phone_type']
		query['location_type'] = data['location_type']
		result = self.performAPICall(query)
		if result['is_error']:
			raise CiviAPIException(result['error_message'])
		if result['count']>1:
			self.log("Contact %s has more then one [%s/%s] phone number. Delivering first!" % (query.get('contact_id', 'n/a'), query.get('phone_type', 'n/a'), query.get('location_type', 'n/a')),
				logging.ERROR, 'pycivi', 'get', 'Phone', query.get('contact_id', None), None, time.time()-timestamp)
		elif result['count']==0:
			return None
		return self._createEntity('Phone', result['values'][0])

	def getPhoneNumbers(self, contact_id, location_type_id=None):
		timestamp = time.time()
		query = dict()
		query['action']			 	= 'get'
		query['entity'] 			= 'Phone'
		query['contact_id'] 		= contact_id
		if location_type_id:
			query['location_type_id'] 	= location_type_id

		result = self.performAPICall(query)
		if result['is_error']:
			raise CiviAPIException(result['error_message'])

		phones = list()
		for phone_data in result['values']:
			phones.append(self._createEntity('Phone', phone_data))

		self.log("Found %d phone numbers (type %s) for contact %s." % (len(phones), location_type_id, query.get('contact_id', 'n/a')),
			logging.DEBUG, 'pycivi', 'get', 'Phone', query.get('contact_id', None), None, time.time()-timestamp)

		return phones

	def createPhoneNumber(self, data):
		timestamp = time.time()
		query = dict(data)
		query['action'] = 'create'
		query['entity'] = 'Phone'
		result = self.performAPICall(query)
		if result['is_error']:
			raise CiviAPIException(result['error_message'])
		return self._createEntity('Phone', result['values'][0])


	def getOrCreatePrefix(self, prefix_text):
		"""
		Looks up or creates the given individual prefix
		"""
		timestamp = time.time()
		option_group = 'individual_prefix'
		option_group_id = self.getOptionGroupID(option_group)
		if not option_group_id:
			self.log("Option group '%s' not found!" % option_group,
				logging.ERROR, 'pycivi', 'getOrCreatePrefix', 'OptionGroup', None, None, time.time()-timestamp)
			return

		greeting_id = self.getOptionValueID(option_group_id, prefix_text)
		if greeting_id:
			self.log("Prefix '%s' already exists [%s]" % (prefix_text, greeting_id),
				logging.INFO, 'pycivi', 'getOrCreatePrefix', 'OptionValue', None, None, time.time()-timestamp)
		if not greeting_id:
			greeting_id = self.setOptionValue(option_group_id, prefix_text)
			self.log("Prefix '%s' created [%s]" % (prefix_text, greeting_id),
				logging.INFO, 'pycivi', 'getOrCreatePrefix', 'OptionValue', None, None, time.time()-timestamp)

		return greeting_id


	def getOrCreateGreeting(self, greeting_text, postal=False):
		"""
		Looks up or creates the given greeting for postal or email greetign
		"""
		timestamp = time.time()
		if postal:
			option_group = 'email_greeting'
		else:
			option_group = 'postal_greeting'

		option_group_id = self.getOptionGroupID(option_group)
		if not option_group_id:
			self.log("Option group '%s' not found!" % option_group,
				logging.ERROR, 'pycivi', 'getOrCreateGreeting', 'OptionGroup', None, None, time.time()-timestamp)
			return

		greeting_id = self.getOptionValueID(option_group_id, greeting_text)
		if greeting_id:
			self.log("Greeting '%s' already exists [%s]" % (greeting_text, greeting_id),
				logging.INFO, 'pycivi', 'getOrCreateGreeting', 'OptionValue', None, None, time.time()-timestamp)
		if not greeting_id:
			greeting_id = self.setOptionValue(option_group_id, greeting_text)
			self.log("Greeting '%s' created [%s]" % (greeting_text, greeting_id),
				logging.INFO, 'pycivi', 'getOrCreateGreeting', 'OptionValue', None, None, time.time()-timestamp)

		return greeting_id


	def getOrCreateTagID(self, tag_name, description = None):
		query = { 'entity': 'Tag',
				  'action': 'get',
				  'name' : tag_name}
		result = self.performAPICall(query)
		if result['count']>1:
			raise CiviAPIException("Tag name query result not unique, this should not happen!")
		elif result['count']==1:
			return result['values'][0]['id']
		else:
			# tag doesn't exist => create
			query['action'] = 'create'
			if description:
				query['description'] = description
			result = self.performAPICall(query)
			return result['values'][0]['id']


	def getOrCreateGroupID(self, group_name, description = None):
		query = { 'entity': 'Group',
				  'action': 'get',
				  'title' : group_name}
		result = self.performAPICall(query)
		if result['count']>1:
			raise CiviAPIException("Group name query result not unique, this should not happen!")
		elif result['count']==1:
			return result['values'][0]['id']
		else:
			# group doesn't exist => create
			query['action'] = 'create'
			query['group_type'] = '[2]'  # set as Mailing Group
			if description:
				query['description'] = description
			result = self.performAPICall(query)
			return result['values'][0]['id']


	def getContactTagIds(self, entity_id):
		query = { 'entity': 'EntityTag',
				  'contact_id' : entity_id,
				  'action' : 'get',
				  }
		result = self.performAPICall(query)
		if result['is_error']:
			raise CiviAPIException(result['error_message'])
		else:
			count = result['count']
			tags = set()
			for entry in result['values']:
				tags.add(entry['tag_id'])
			if len(tags)!=count:
				raise CiviAPIException("Error: tag count does not match number of delivered tags!")
			return tags


	def getContactGroupIds(self, entity_id):
		query = { 'entity': 'GroupContact',
				  'contact_id' : entity_id,
				  'action' : 'get',
				  }
		result = self.performAPICall(query)
		count = result['count']
		groups = set()
		for entry in result['values']:
			groups.add(entry['group_id'])
		if len(groups)!=count:
			raise CiviAPIException("Error: group count does not match number of delivered group!")
		return groups


	def tagContact(self, entity_id, tag_id, value=True):
		timestamp = time.time()
		query = { 'entity': 'EntityTag',
				  'contact_id' : entity_id,
				  'tag_id' : tag_id,
				  }
		if value:
			query['action'] = 'create'
		else:
			query['action'] = 'delete'
		result = self.performAPICall(query)
		if result['is_error']:
			raise CiviAPIException(result['error_message'])
		elif result.get('added', False):
			self.log("Added new tag(%s) to contact(%s)" % (tag_id, entity_id),
				logging.INFO, 'pycivi', query['action'], 'EntityTag', entity_id, tag_id, time.time()-timestamp)
		elif result.get('removed', False):
			self.log("Removed tag(%s) from contact(%s)" % (tag_id, entity_id),
				logging.INFO, 'pycivi', query['action'], 'EntityTag', entity_id, tag_id, time.time()-timestamp)
		else:
			self.log("No tags changed for contact#%s" % entity_id,
				logging.DEBUG, 'pycivi', query['action'], 'EntityTag', entity_id, tag_id, time.time()-timestamp)


	def setGroupMembership(self, entity_id, group_id, value=True):
		timestamp = time.time()
		query = { 'entity': 'GroupContact',
				  'contact_id' : entity_id,
				  'group_id' : group_id,
				  }
		if value:
			query['action'] = 'create'
		else:
			query['action'] = 'delete'
		result = self.performAPICall(query)
		if result['is_error']:
			raise CiviAPIException(result['error_message'])
		elif result.get('added', False):
			self.log("Added contact (%s) to group (%s)." % (entity_id, group_id),
				logging.INFO, 'pycivi', query['action'], 'GroupContact', entity_id, group_id, time.time()-timestamp)
		elif result.get('removed', False):
			self.log("Removed contact (%s) from group (%s)." % (entity_id, group_id),
				logging.INFO, 'pycivi', query['action'], 'GroupContact', entity_id, group_id, time.time()-timestamp)
		else:
			self.log("No group membership changed for contact (%s)" % entity_id,
				logging.DEBUG, 'pycivi', query['action'], 'GroupContact', entity_id, group_id, time.time()-timestamp)




	def _createEntity(self, entity_type, attributes):
		if entity_type==etype.CONTACT:
			return CiviContactEntity(entity_type, attributes.get('id', None), self, attributes)
		elif entity_type==etype.CONTRIBUTION:
			return CiviContributionEntity(entity_type, attributes.get('id', None), self, attributes)
		elif entity_type==etype.PHONE:
			return CiviPhoneEntity(entity_type, attributes.get('id', None), self, attributes)
		elif entity_type==etype.CAMPAIGN:
			return CiviCampaignEntity(entity_type, attributes.get('id', None), self, attributes)
		else:
			return CiviEntity(entity_type, attributes.get('id', None), self, attributes)

