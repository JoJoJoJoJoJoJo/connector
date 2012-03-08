# -*- encoding: utf-8 -*-
###############################################################################
#                                                                             #
#   file_exchange for OpenERP                                                 #
#   Copyright (C) 2012 Akretion Emmanuel Samyn <emmanuel.samyn@akretion.com>  #
#                                                                             #
#   This program is free software: you can redistribute it and/or modify      #
#   it under the terms of the GNU Affero General Public License as            #
#   published by the Free Software Foundation, either version 3 of the        #
#   License, or (at your option) any later version.                           #
#                                                                             #
#   This program is distributed in the hope that it will be useful,           #
#   but WITHOUT ANY WARRANTY; without even the implied warranty of            #
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the             #
#   GNU Affero General Public License for more details.                       #
#                                                                             #
#   You should have received a copy of the GNU Affero General Public License  #
#   along with this program.  If not, see <http://www.gnu.org/licenses/>.     #
#                                                                             #
###############################################################################
from tools.safe_eval import safe_eval as eval
from osv import osv, fields
import netsvc
from base_external_referentials.external_osv import ExternalSession
from base_file_protocole.base_file_protocole import FileCsvReader
from tempfile import TemporaryFile
from encodings.aliases import aliases
from tools.translate import _

class file_exchange(osv.osv):
    _name = "file.exchange"
    _description = "file exchange"

    def get_export_default_fields_values(self, cr, uid, id, context=None):
        if isinstance(id, list):
            id = id[0]
        res = {}
        method = self.browse(cr, uid, id, context=context)
        for field in method.field_ids:
            if field.advanced_default_value:
                space = {'self': self,
                         'cr': cr,
                         'uid': uid,
                         'id': id,
                         'context': context,
                    }
                try:
                    exec field.advanced_default_value in space
                except Exception, e:
                    raise osv.except_osv(_('Error !'), _('Error when evaluating advanced default value: %s \n Exception: %s' %(fields.name,e)))
                res[field.name] = space.get('result', False)
            elif field.default_value:
                res[field.name] = field.default_value
        return res

    def get_import_default_fields_values(self, cr, uid, method_id, context=None):
        res = {}
        method = self.browse(cr, uid, method_id, context=context)
        for field in method.import_default_field:
            res[field.import_default_field.name] = field.import_default_value
        return res
    
    def _get_external_file_resources(self, cr, uid, external_session, filepath, filename, format, fields_name=None, mapping=None, context=None):
        external_file = external_session.connection.get(filepath, filename)
        method_id = context['file_exchange_id']
        method = self.browse(cr, uid, method_id, context=context)
        if format in ['csv_no_header','csv']:
            field_model = {}
            alternative_key = False
            for field_id in method.field_ids:
                if not field_id.mapping_line_id:
                    continue
                field_model[field_id.name] = "%s_%s" %(field_id.mapping_line_id.related_model_id.model, field_id.mapping_line_id.mapping_id.id)
                if field_id.alternative_key and field_id.mapping_line_id.related_model_id.model == method.model_id.model:
                    alternative_key = field_id.name
            res = FileCsvReader(external_file, fieldnames= format=='csv_no_header' and fields_name or None, delimiter=method.delimiter.encode('utf-8'), encoding = method.encoding)
            mapping_id = self.pool.get('external.mapping').search(cr, uid, [('model_id', '=', method.model_id.id)], context=context)[0]
            method_model_name = "%s_%s" %(method.model_id.model, mapping_id)
            mapping_tree = self._get_mapping_tree(cr, uid, mapping_id, context=context)
            result = {}
            merge_keys = [key for mapping in mapping_tree for key in mapping if mapping[key]['type'] in ['one2many','many2many']]
            for line in res:
                res_line = {}
                for key in line:
                    if key in field_model:
                        value_model = field_model[key]
                        if value_model != method_model_name:
                            if not value_model in res_line:
                                res_line[value_model] = {}
                            res_line[value_model][key] = line[key]
                        else: 
                            res_line[key] = line[key]
                for mapping in mapping_tree:
                    for key, value in mapping.items():
                        if value['parent_name'] != method_model_name:
                            res_line[value['parent_name']][key] = res_line[key]
                            del res_line[key] 
                if line[alternative_key] in result:
                    for key in merge_keys:
                        result[line[alternative_key]][key].append(res_line[key])
                else:
                    result[line[alternative_key]] = res_line
                    for key in merge_keys:
                        result[line[alternative_key]][key] =  [result[line[alternative_key]][key]]
            result = [result[key] for key in result]         
        return result
    
    def _get_mapping_tree(self, cr, uid, mapping_id, parent_name=None, mapping_type=None, context=None):
        result = []
        mapping = self.pool.get('external.mapping').browse(cr, uid, mapping_id, context=context)
        mapping_name = "%s_%s" %(mapping.model_id.model, mapping.id)
        for mapping_line in mapping.mapping_ids:
            if mapping_line.evaluation_type == 'sub-mapping':
                res = self._get_mapping_tree(cr, uid, mapping_line.child_mapping_id.id, mapping_name, mapping_line.external_type, context=context)
                result = res + result
        if parent_name:
            result.append({mapping_name : {'parent_name' : parent_name, 'type' : mapping_type}})
        return result

    def start_task(self, cr, uid, ids, context=None):
        for method in self.browse(cr, uid, ids, context=context):
            if method.type == 'in':
                self._import_files(cr, uid, method.id, context=context)
            elif method.type == 'out':
                self._export_files(cr, uid, method.id, context=context)
        return True

    def _import_files(self, cr, uid, method_id, context=None):
        if not context:
            context={}
        context['file_exchange_id'] = method_id
        file_fields_obj = self.pool.get('file.fields')
        method = self.browse(cr, uid, method_id, context=context)
        defaults = self.get_import_default_fields_values(cr, uid, method_id, context=context)
        external_session = ExternalSession(method.referential_id)
        mapping = {method.model_id.model : self.pool.get(method.model_id.model)._get_mapping(cr, uid, method.referential_id.id, context=context)}

        fields_name_ids = file_fields_obj.search(cr, uid, [['file_id', '=', method.id]], context=context)
        fields_name = [x['name'] for x in file_fields_obj.read(cr, uid, fields_name_ids, ['name'], context=context)]

        result = {"create_ids" : [], "write_ids" : []}
        list_filename = external_session.connection.search(method.folder_path, method.filename)
        if not list_filename:
            external_session.logger.info("No file '%s' found on the server"%(method.filename,))
        for filename in list_filename:
            external_session.logger.info("Start to import the file %s"%(filename,))
            resources = self._get_external_file_resources(cr, uid, external_session, method.folder_path, filename, method.format, fields_name, mapping=mapping, context=context)
            res = self.pool.get(method.model_id.model)._record_external_resources(cr, uid, external_session, resources, defaults=defaults, mapping=mapping, context=context)
            external_session.connection.move(method.folder_path, method.archive_folder_path, filename)
            external_session.logger.info("Finish to import the file %s"%(filename,))
        return result

    def _check_if_file_exist(self, cr, uid, external_session, folder_path, filename, context=None):
        exist = external_session.connection.search(folder_path, filename)
        if exist:
            raise osv.except_osv(_('Error !'), _('The file "%s" already exist in the folder "%s"' %(filename, folder_path)))
        return False

    def _export_files(self, cr, uid, method_id, context=None):
    #TODO refactor this method toooooo long!!!!!
        def flat_resources(resources):
            result=[]
            for resource in resources:
                row_to_flat = False
                for key, value in resource.items():
                    if key != False:
                        if 'hidden_field_to_split_' in key:
                            if isinstance(value, list):
                                if row_to_flat:
                                    raise osv.except_osv(_('Error !'), _('Can not flat two row in the same resource'))
                                row_to_flat = value
                            elif isinstance(value, dict):
                                for k,v in flat_resources([value])[0].items():
                                    resource[k] = v
                            del resource[key]
                if row_to_flat:
                    for elements in row_to_flat:
                        tmp_dict = resource.copy()
                        tmp_dict.update(flat_resources([elements])[0])
                        result.append(tmp_dict)
                else:
                    result.append(resource)
            return result

        file_fields_obj = self.pool.get('file.fields')

        method = self.browse(cr, uid, method_id, context=context)
    #=== Get connection
        external_session = ExternalSession(method.referential_id)
        sequence_obj = self.pool.get('ir.sequence')
        d = sequence_obj._interpolation_dict()
        filename = sequence_obj._interpolate(method.filename, d)
    #=== Check if file already exist in specified folder. If so, raise an alert
        self._check_if_file_exist(cr, uid, external_session, method.folder_path, filename, context=context)
    #=== Start export
        external_session.logger.info("Start to export %s"%(method.name,))
        model_obj = self.pool.get(method.model_id.model)
        defaults = self.get_export_default_fields_values(cr, uid, method_id, context=context)
        encoding = method.encoding
    #=== Get external file ids and fields
        fields_name_ids = file_fields_obj.search(cr, uid, [['file_id', '=', method.id]], context=context)
        fields_info = file_fields_obj.read(cr, uid, fields_name_ids, ['name', 'mapping_line_id'], context=context)
        print "fields_info: ",fields_info
    #=== Get lines that need to be mapped
        mapping_line_filter_ids = [x['mapping_line_id'][0] for x in fields_info if x['mapping_line_id']]
        fields_name = [x['name'] for x in fields_info]
    #=== Apply filter
        #TODO add a filter
        ids_filter = "()" # In case not filter is filed in the form
        if method.search_filter != False:
            ids_filter = method.search_filter
        ids_to_export = model_obj.search(cr, uid, eval(ids_filter), context=context)
    #=== Start mapping
        mapping = {model_obj._name : model_obj._get_mapping(cr, uid, external_session.referential_id.id, convertion_type='from_openerp_to_external', mapping_line_filter_ids=mapping_line_filter_ids, context=context)}
        fields_to_read = [x['internal_field'] for x in mapping[model_obj._name]['mapping_lines']]
        # TODO : CASE fields_to_read is False !!!
        resources = model_obj._get_oe_resources_into_external_format(cr, uid, external_session, ids_to_export, mapping=mapping, mapping_line_filter_ids=mapping_line_filter_ids, fields=fields_to_read, defaults=defaults, context=context)
        print "resources: ",resources
    #=== Check if content to export
        if not resources:
            external_session.logger.info("Not data to export for %s"%(method.name,))
            return True
    #=== Write CSV file
        if method.format == 'csv':
            output_file = TemporaryFile('w+b')
            fields_name = [x.encode(encoding) for x in fields_name]
            print "fields_name: ",fields_name
            dw = csv.DictWriter(output_file, fieldnames=fields_name, delimiter=';', quotechar='"')
#            dw.writeheader() TODO : only for python >= 2.7
            row = {}
        #=== Write header
            for name in fields_name:
                row[name.encode(encoding)] = name.encode(encoding)
            dw.writerow(row)
        #=== Write content
            resources = flat_resources(resources)
            for resource in resources:
                row = {}
                for k,v in resource.items():
                    if k!=False:
                        try:
                            if isinstance(v, unicode) and v!=False:
                                row[k.encode(encoding)] = v.encode(encoding)
                            else:
                                row[k.encode(encoding)] = v
                        except:
                            row[k.encode(encoding)] = "ERROR"
                        #TODO raise an error correctly
                print "=====> row: ",row
                dw.writerow(row)
            output_file.seek(0)
        method.start_action('action_after_all', model_obj, ids_to_export, context=context)

    #=== Export file
        external_session.connection.send(method.folder_path, filename, output_file)
        external_session.logger.info("File transfert have been done succesfully %s"%(method.name,))
        raise osv.except_osv(_('Export succesfull'), _('File transfert have been done succesfully %s' %(method.name,)))
        return True

    def start_action(self, cr, uid, id, action_name, self_object, object_ids, context=None):
        if isinstance(id, list):
            id = id[0]
        method = self.browse(cr, uid, id, context=context)
        action_code = getattr(method, action_name)
        if action_code:
            space = {'self': self_object,
                     'cr': cr,
                     'uid': uid,
                     'ids': object_ids,
                     'context': context,
                }
            try:
                exec action_code in space
            except Exception, e:
                raise osv.except_osv(_('Error !'), _('Error can not apply the python action default value: %s \n Exception: %s' %(method.name,e)))
        return True

    def _get_encoding(self, cr, user, context=None):
        result = [(x, x.replace('_', '-')) for x in set(aliases.values())]
        result.sort()
        return result

    _columns = {
        'name': fields.char('Name', size=64, help="Exchange description like the name of the supplier, bank,...", require=True),
        'type': fields.selection([('in','IN'),('out','OUT'),], 'Type',help=("IN for files coming from the other system"
                                                                "and to be imported in the ERP ; OUT for files to be"
                                                                "generated from the ERP and send to the other system")),
        'model_id':fields.many2one('ir.model', 'Model',help="OpenEPR main object from which all the fields will be related", require=True),
        'format' : fields.selection([('csv','CSV'),('csv_no_header','CSV WITHOUT HEADER')], 'File format'),
        'referential_id':fields.many2one('external.referential', 'Referential',help="Referential to use for connection and mapping", require=True),
        'scheduler':fields.many2one('ir.cron', 'Scheduler',help="Scheduler that will execute the cron task"),
        'search_filter':  fields.char('Search Filter', size=256),
        'filename': fields.char('Filename', size=128, help="Filename will be used to generate the output file name or to read the incoming file. It is possible to use variables (check in sequence for syntax)", require=True),
        'folder_path': fields.char('Folder Path', size=128, help="folder that containt the incomming or the outgoing file"),
        'archive_folder_path': fields.char('Archive Folder Path', size=128, help="if a path is set when a file is imported the file will be automatically moved to this folder"),
        'encoding': fields.selection(_get_encoding, 'Encoding', require=True),
        'field_ids': fields.one2many('file.fields', 'file_id', 'Fields'),
        'action_before_all': fields.text('Action Before All', help="This python code will executed after the import/export"),
        'action_after_all': fields.text('Action After All', help="This python code will executed after the import/export"),
        'action_before_each': fields.text('Action Before Each', help="This python code will executed after each element of the import/export"), 
        'action_after_each': fields.text('Action After Each', help="This python code will executed after each element of the import/export"),
        'delimiter':fields.char('Fields delimiter', size=64, help="Delimiter used in the CSV file"),
        'import_default_field':fields.one2many('file.default.import.values', 'file_id', 'Default Field'),
    }

    # Method to export the exchange file
    def create_exchange_file(self, cr, uid, ids, context={}):
        csv_file = "\"id\",\"name\",\"referential_id:id\",\"scheduler:id\",\"type\",\"model_id:id\",\"encoding\",\"format\",\"search_filter\",\"folder_path\",\"filename\"\n"
        current_file = self.browse(cr, uid, ids)[0]
        generated_id = "\"" + current_file.name + "_" + current_file.referential_id.name + "_" + current_file.model_id.name + "\","
        csv_file += generated_id.replace(' ', '_')
        csv_file += "\"" + current_file.name + "\","
        csv_file += "\"" + current_file.referential_id.get_external_id(context=context)[current_file.referential_id.id] + "\",\""
        if current_file.scheduler :
            csv_file += current_file.scheduler.get_external_id(context=context)[current_file.scheduler.id]
        csv_file += "\",\"" + current_file.type + "\","
        csv_file += "\"" + current_file.model_id.get_external_id(context=context)[current_file.model_id.id] + "\","
        csv_file += "\"" + current_file.encoding + "\","
        csv_file += "\"" + current_file.format + "\",\""
        if current_file.search_filter:
            csv_file += current_file.search_filter
        csv_file += "\",\"" 
        if current_file.folder_path:
            csv_file += current_file.folder_path
        csv_file += "\",\"" + current_file.filename + "\""

        raise osv.except_osv(_('Fields'), _(csv_file))
        return True
        
    # Method to export the mapping file
    def create_file_fields(self, cr, uid, ids, context={}):
        csv_file = "\"id\",\"name\",\"custom_name\",\"sequence\",\"mapping_line_id:id\",\"file_id:id\",\"default_value\",\"advanced_default_value\"\n"
        current_file = self.browse(cr, uid, ids)[0]
        for field in current_file.field_ids:
            generated_id = "\"" + field.file_id.name + "_" + field.name + "_" + str(field.sequence) + "\","
            print "gen_id: ",generated_id
            csv_file += generated_id.replace(' ', '_')
            csv_file += "\"" + field.name + "\",\""
            if field.custom_name:
                csv_file += field.custom_name
            csv_file += "\",\""
            csv_file += str(field.sequence)
            csv_file += "\",\""
            csv_file += "TODO" #mapping_line_id
            csv_file += "\",\""
            csv_file += field.file_id.get_external_id(context=context)[field.file_id.id]
            csv_file += "\",\""
            if field.default_value:
                csv_file += field.default_value
            csv_file += "\",\""
            if field.advanced_default_value:
                csv_file += field.advanced_default_value
            csv_file += "\"\n"
        raise osv.except_osv(_('Fields'), _(csv_file))
        return True

file_exchange()

class file_fields(osv.osv):
    _name = "file.fields"
    _description = "file fields"
    _order='sequence'

    def _clean_vals(self, vals):
        if vals.get('custom_name'):
            vals['mapping_line_id'] = False
        elif vals.get('mapping_line_id'):
            vals['custom_name'] = False
        return vals

    def create(self, cr, uid, vals, context=None):
        vals = self._clean_vals(vals)
        return super(file_fields, self).create(cr, uid, vals, context=context)

    def write(self, cr, uid, ids, vals, context=None):
        vals = self._clean_vals(vals)
        return super(file_fields, self).write(cr, uid, ids, vals, context=context)

    def _name_get_fnc(self, cr, uid, ids, name, arg, context=None):
        res = {}
        for file_field in self.browse(cr, uid, ids, context):
            res[file_field.id] = file_field.mapping_line_id and file_field.mapping_line_id.external_field or file_field.custom_name
        return res

    _columns = {
        #TODO the field name should be autocompleted bey the external field when selecting a mapping
        'name': fields.function(_name_get_fnc, type="char", string='Name', method=True),
        'custom_name': fields.char('Custom Name', size=64),
        'sequence': fields.integer('Sequence', required=True, help="The sequence field is used to define the order of the fields"),
        #TODO add a filter only fields that belong to the main object or to sub-object should be available
        'mapping_line_id': fields.many2one('external.mapping.line', 'OpenERP Mapping', domain = "[('referential_id', '=', parent.referential_id)]"),
        'file_id': fields.many2one('file.exchange', 'File Exchange', require="True"),
        'default_value': fields.char('Default Value', size=64),
        'advanced_default_value': fields.text('Advanced Default Value', help=("This python code will be evaluate and the value"
                                                                        "in the varaible result will be used as defaut value")),
        'alternative_key': fields.related('mapping_line_id', 'alternative_key', type='boolean', string='Alternative Key'),
        'is_compulsary' : fields.boolean('Is compulsary', help="Is this field compulsary in the exchange ?"),
    }

file_fields()

class file_default_import_values(osv.osv):
    _name = "file.default.import.values"
    _description = "file default import values"

    _columns = {
        'import_default_field':fields.many2one('ir.model.fields', 'Default Field'),
        'import_default_value':fields.char('Default Value', size=128),
        'file_id': fields.many2one('file.exchange', 'File Exchange', require="True"),
    }

