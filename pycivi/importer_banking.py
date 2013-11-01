#!/usr/bin/python
# -*- coding: utf-8 -*-

from importer import *

import CiviCRM, entity_type
import csv
import codecs
import threading
import logging
import time
import traceback
import datetime
import sha



def import_bank_accounts(civicrm, record_source, parameters=dict()):
	"""
	Will import bank account references (for the CiviBanking extension)

	Expected fields:
	 'id' (bank account) or 'contact_id' (contact) or 'external_identifier' (contact)
	 'created_date'		- will be set as created date
	 'modified_date'	- if no created_date is set, this will be used as created_date if a new entry is created
	 'description'		- account description
	 'data_raw'			- unparsed account information
	 'data_parsed'		- parsed account information

	Reference fields:
	 'IBAN'				- IBAN account reference
	 'NBAN_??'			- national bank account reference (?? = country code)

	Parameters:
	 'reference_mode'	- 'overwrite' = preexisting references will be deleted
	 					- 'update'	  = only replace given references 
	"""
	for record in record_source:
		if not record.has_key('id'):
			# we'll have to look for an ID
			if not record.has_key('contact_id'):
				# ...an external one, apparently
				record['contact_id'] = civicrm.getContactID({'external_identifier': record['external_identifier']})

			if not record.get('contact_id', None):
				civicrm.log("Contact not found for bank account creation not found!",
					logging.WARN, 'importer_banking', 'import_bank_accounts', 'Contact', None, None, 0)
				continue

		# now for the references:
		refs = dict()
		for key in record:
			if key=='IBAN' or key[:4] == 'NBAN':
				value = record.get(key, None)
				if value:
					refs[key] = record[key]

		if not refs:
			civicrm.log("Bank account record has no references. Account will NOT be created!",
				logging.WARN, 'importer_banking', 'import_bank_accounts', 'BankingAccount', None, None, 0)
			continue

		# prepare the account data
		account_data = dict()
		for key in ['id', 'contact_id', 'created_date', 'modified_date', 'description', 'data_raw', 'data_parsed']:
			if key in record:
				account_data[key] = record[key]

		# find the account by its reference
		account_reference_type = refs.keys()[0]
		account_reference_data = dict()
		account_reference_data['reference'] = refs[account_reference_type]
		account_reference_data['reference_type_id'] = civicrm.getOptionValueID(civicrm.getOptionGroupID('civicrm_banking.reference_types'), account_reference_type)
		account_reference = civicrm.getEntity('BankingAccountReference', account_reference_data, ['reference','reference_type_id'])

		if not account_reference:
			# create a new account
			account = civicrm.createOrUpdate('BankingAccount', account_data, update_type='update', primary_attributes=[u'id', u'contact_id'])

			# adjust timestamps, if necessary (they get set automatically to "now" on creation)
			timestamps = dict()
			if account_data.has_key('modified_date'):
				timestamps['modified_date'] = datetime.datetime.strptime(account_data['modified_date'], "%Y-%m-%d").strftime('%Y%m%d%H%M%S')
			if account_data.has_key('created_date'):
				timestamps['created_date'] = datetime.datetime.strptime(account_data['created_date'], "%Y-%m-%d").strftime('%Y%m%d%H%M%S')
			account.update(timestamps, True)
			civicrm.log("Created new bank account [%s]" % account.get('id'),
				logging.INFO, 'importer_banking', 'import_bank_accounts', 'BankingAccount', account.get('id'), account.get('contact_id'), 0)
		else:
			# update the found account
			account = civicrm.getEntity('BankingAccount', {'id': account_reference.get('ba_id')}, primary_attributes=['id','contact_id'])

			# we found this account be this account reference, we don't have to check that again
			refs.pop(account_reference_type)

			# FIXME: the same creation date would trigger an update, since the format is different
			account_data.pop('created_date')
			
			if account.update(account_data):
				# something has changed: update modification date
				if not account_data.has_key('modified_date'):
					account.set('modified_date', datetime.datetime.now().strftime('%Y%m%d%H%M%S'))
				else:
					account.set('modified_date', account_data['modified_date'])
				account.store()
				civicrm.log("Updated bank account [%s]" % account.get('id'),
					logging.INFO, 'importer_banking', 'import_bank_accounts', 'BankingAccount', account.get('id'), account.get('contact_id'), 0)
			else:
				civicrm.log("No need to update bank account [%s]" % account.get('id'),
					logging.INFO, 'importer_banking', 'import_bank_accounts', 'BankingAccount', account.get('id'), account.get('contact_id'), 0)

		# create/verify the references
		for key in refs:
			reference_type_id = civicrm.getOptionValueID(civicrm.getOptionGroupID('civicrm_banking.reference_types'), key)
			if not reference_type_id:
				civicrm.log("Reference type ID for '%s' not found! Ignored." % key,
					logging.WARN, 'importer_banking', 'import_bank_accounts', 'BankingAccountReference', None, None, 0)
				continue
			reference_data = {u'reference_type_id': reference_type_id, u'ba_id': account.get('id'), u'reference': refs[key]}
			reference = civicrm.createOrUpdate('BankingAccountReference', reference_data, update_type='update', primary_attributes=[u'ba_id', u'reference_type_id'])
			civicrm.log("Verified or added %s reference for bank account [%s]." % (key, account.get('id')),
				logging.INFO, 'importer_banking', 'import_bank_accounts', 'BankingAccountReference', reference.get('id'), account.get('id'), 0)





def import_sepa_mandates(civicrm, record_source, parameters=dict()):
	"""
	Will import SEPA mandates (for the sepa_dd extension).

	Expected fields:
	 (Mandate): "invoice_id", "reference", "date", "creditor_id", "iban", "bic", "type", "creation_date", "validation_date", "is_enabled"
	 (Recurring Contribution): "is_email_receipt", "payment_instrument_id", "financial_type_id", "payment_processor_id", "auto_renew", "failure_count", "cycle_day", "is_test", "contribution_status_id", "trxn_id", "contact_id", "amount", "currency", "frequency_unit", "frequency_interval", "installments", "start_date", "create_date", "modified_date"

	Need to be passed via parameters:
	 sepa_creditor_id, payment_instrument_id, payment_processor_id

	The importer will create a recurring contribution, the (first) associated contribution and finally the mandate.
	In a last step, the mandate will be activated, if is_enabled==1
	"""
	mandate_keys = set(["invoice_id", "reference", "date", "creditor_id", "iban", "bic", "type", "creation_date", "validation_date"]) # "is_enabled", 
	rcontrib_keys = set(["is_email_receipt", "payment_instrument_id", "financial_type_id", "payment_processor_id", "auto_renew", "failure_count", "cycle_day", "is_test", "contribution_status_id", "trxn_id", "contact_id", "amount", "currency", "frequency_unit", "frequency_interval", "installments", "start_date", "create_date", "modified_date"])
	contrib_keys = set(["contact_id", "financial_type_id", "contribution_page_id", "payment_instrument_id", "total_amount", "non_deductible_amount", "fee_amount", "net_amount", "trxn_id", "invoice_id", "currency", "cancel_date", "cancel_reason", "receipt_date", "thankyou_date", "source", "amount_level", "honor_contact_id", "is_test", "is_pay_later", "honor_type_id", "address_id", "check_number", "campaign_id"]) # contribution_recur_id, contribution_status_id

	# perform some sanity checks
	if not parameters.has_key('sepa_creditor_id'): 
		civicrm.log("No sepa_creditor_id specified for import.",
			logging.ERROR, 'importer', 'import_sepa_mandates', 'SepaMandate', None, None, 0)
		return
	if not parameters.has_key('payment_instrument_id'): 
		civicrm.log("No payment_instrument_id specified for import.",
			logging.ERROR, 'importer', 'import_sepa_mandates', 'SepaMandate', None, None, 0)
		return
	if not parameters.has_key('payment_processor_id'): 
		civicrm.log("No payment_processor_id specified for import.",
			logging.ERROR, 'importer', 'import_sepa_mandates', 'SepaMandate', None, None, 0)
		return


	for record in record_source:
		# first: find contact
		timestamp = time.time()
		contact_id = civicrm.getContactID(record)
		if not contact_id:
			civicrm.log("Contact not found: ID %s" % contact_id,
				logging.WARN, 'importer', 'import_sepa_mandates', 'Contact', contact_id, None, 0)
			continue

		# split/prepare records
		mandate_record = dict()
		for key in (mandate_keys & set(record.keys())):
			mandate_record[key] = record[key]
		mandate_record['entity_table'] = 'civicrm_contribution_recur'
		mandate_record['creditor_id'] = parameters['sepa_creditor_id']
		mandate_record['contact_id'] = contact_id
		mandate_record['date'] = mandate_record.get('date', str(datetime.datetime.now()).split(' ')[0])
		mandate_record['is_enabled'] = 0

		rcontrib_record = dict()
		for key in (rcontrib_keys & set(record.keys())):
			rcontrib_record[key] = record[key]
		rcontrib_record['entity_record'] = rcontrib_record.get('entity_record', 0)
		rcontrib_record['is_email_receipt'] = rcontrib_record.get('is_email_receipt', 0)
		rcontrib_record['is_test'] = rcontrib_record.get('is_test', 0)
		rcontrib_record['contact_id'] = contact_id
		rcontrib_record['payment_instrument_id'] = parameters['payment_instrument_id']
		rcontrib_record['payment_processor_id'] = parameters['payment_processor_id']
		rcontrib_record['modified_date'] = rcontrib_record.get('modified_date', str(datetime.datetime.now()).split(' ')[0])

		contrib_record = dict()
		for key in (contrib_keys & set(record.keys())):
			contrib_record[key] = record[key]
		contrib_record['contact_id'] = contact_id
		contrib_record['total_amount'] = record['amount']
		contrib_record['non_deductible_amount'] = record['amount']
		contrib_record['payment_instrument_id'] = parameters['payment_instrument_id']
		contrib_record['payment_processor_id'] = parameters['payment_processor_id']
		contrib_record['receive_date'] = rcontrib_record['start_date']

		# then, see if we already have a mandate
		mandate = civicrm.getEntity('SepaMandate', mandate_record, ['reference'])
		if not mandate:
			civicrm.log("Creating new mandate '%s'..." % mandate_record['reference'],
				logging.INFO, 'importer', 'import_sepa_mandates', 'SepaMandate', None, None, 0)
			# to create a mandate, first create a recurring contribution
			hashv = sha.new(str(rcontrib_record)).hexdigest()
			if not 'trxn_id' in rcontrib_record:
				rcontrib_record['trxn_id'] = hashv
			if not 'invoice_id' in rcontrib_record:
				rcontrib_record['invoice_id'] = hashv
		else:
			civicrm.log("Updating existing mandate '%s' [%s]..." % (mandate_record['reference'], mandate.get('id')),
				logging.INFO, 'importer', 'import_sepa_mandates', 'SepaMandate', mandate.get('id'), None, 0)
			rcontrib_record['id'] = mandate.get('entity_id')

		# first create (or update) the recurring contribution
		timestamp_r = time.time()
		rcontrib = civicrm.createOrUpdate('ContributionRecur', rcontrib_record, 'update', ['contact_id', 'id', 'invoice_id'])	
		mandate_record['entity_id'] = rcontrib.get('id')
		civicrm.log("Created or updated associated recurring contribution [%s]" % rcontrib.get('id'),
			logging.INFO, 'importer', 'import_sepa_mandates', 'SepaMandate', rcontrib.get('id'), None, time.time()-timestamp_r)

		# then, create (or update) the first contribution is there
		timestamp_c = time.time()
		contrib_record['contribution_recur_id'] = rcontrib.get('id')
		contrib_record['invoice_id'] = rcontrib.get('invoice_id')
		contrib = civicrm.createOrUpdate('Contribution', contrib_record, 'update', ['invoice_id', 'contribution_recur_id'])
		civicrm.log("Created or updated associated (first) contribution [%s]" % contrib.get('id'),
			logging.INFO, 'importer', 'import_sepa_mandates', 'SepaMandate', contrib.get('id'), rcontrib.get('id'), time.time()-timestamp_c)

		# then create (or update) the mandate
		timestamp_m = time.time()
		if mandate:
			mandate.update(mandate_record, True)
			civicrm.log("Updated mandate '%s' [%s]..." % (mandate_record['reference'], mandate.get('id')),
				logging.INFO, 'importer', 'import_sepa_mandates', 'SepaMandate', contrib.get('id'), rcontrib.get('id'), time.time()-timestamp_m)
		else:
			mandate = civicrm.createEntity('SepaMandate', mandate_record)
			civicrm.log("Created mandate '%s' [%s]..." % (mandate_record['reference'], mandate.get('id')),
				logging.INFO, 'importer', 'import_sepa_mandates', 'SepaMandate', mandate.get('id'), None, time.time()-timestamp_m)

		# finally, enable/disable the mandate in a separate step
		if int(mandate.get('is_enabled'))!=int(record['is_enabled']):
			if int(record['is_enabled']):
				civicrm.log("Activating mandate '%s' [%s]..." % (mandate_record['reference'], mandate.get('id')),
					logging.INFO, 'importer', 'import_sepa_mandates', 'SepaMandate', mandate.get('id'), None, 0)
			else:
				civicrm.log("Deactivating mandate '%s' [%s]..." % (mandate_record['reference'], mandate.get('id')),
					logging.INFO, 'importer', 'import_sepa_mandates', 'SepaMandate', mandate.get('id'), None, 0)
		mandate.update({'is_enabled': record['is_enabled']}, True)
		civicrm.log("Done importing/updating mandate '%s' [%s]..." % (mandate_record['reference'], mandate.get('id')),
			logging.INFO, 'importer', 'import_sepa_mandates', 'SepaMandate', mandate.get('id'), None, time.time()-timestamp)



