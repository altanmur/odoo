odoo.define('web.FieldWrapper', function (require) {
    "use strict";

    const { ComponentWrapper } = require('web.OwlCompatibility');
    const field_utils = require('web.field_utils');

    /**
     * This file defines the FieldWrapper component, an extension of ComponentWrapper,
     * needed to instanciate Owl fields inside legacy widgets. This component
     * will be no longer necessary as soon as all legacy widgets using fields will
     * be rewritten in Owl.
     */
    class FieldWrapper extends ComponentWrapper {
        constructor() {
            super(...arguments);

            this._data = {};

            const options = this.props.options || {};
            const record = this.props.record;
            this._data.name = this.props.fieldName;
            this._data.record = record;
            this._data.field = record.fields[this._data.name];
            this._data.viewType = options.viewType || 'default';
            const fieldsInfo = record.fieldsInfo[this._data.viewType];
            this._data.attrs = options.attrs || (fieldsInfo && fieldsInfo[this._data.name]) || {};
            this._data.additionalContext = options.additionalContext || {};
            this._data.value = record.data[this._data.name];
            this._data.recordData = record.data;
            this._data.string = this._data.attrs.string || this._data.field.string || this._data.name;
            this._data.nodeOptions = this._data.attrs.options || {};
            this._data.dataPointID = record.id;
            this._data.res_id = record.res_id;
            this._data.model = record.model;
            this._data.mode = options.mode || "readonly";
            this._data._isValid = true;
            this._data.lastSetValue = undefined;
            this._data.formatType = this._data.attrs.widget in field_utils.format ?
                                this._data.attrs.widget :
                                this._data.field.type;
            this._data.formatOptions = {};
            this._data.parseOptions = {};
            if (this._data.attrs.decorations) {
                this._data.resetOnAnyFieldChange = true;
            }
            for (const key in this._data) {
                Object.defineProperty(this, key, {
                    get: () => (this.el ? this.componentRef.comp : this._data)[key],
                });
            }
        }

        //----------------------------------------------------------------------
        // Getters
        //----------------------------------------------------------------------

        get $el() {
            return $(this.el);
        }
        get fieldDependencies() {
            return this.Component.fieldDependencies;
        }
        get resetOnAnyFieldChange() {
            return this.Component.resetOnAnyFieldChange;
        }
        get specialData() {
            return this.Component.specialData;
        }
        get supportedFieldTypes() {
            return this.Component.supportedFieldTypes;
        }
        get description() {
            return this.Component.description;
        }
        get noLabel() {
            return this.Component.noLabel;
        }

        //----------------------------------------------------------------------
        // Public
        //----------------------------------------------------------------------

        activate(options) {
            return this.componentRef.comp.activate(options);
        }
        commitChanges() {
            return this.componentRef.comp.commitChanges();
        }
        getFocusableElement() {
            return $(this.componentRef.comp.focusableElement);
        }
        isEmpty() {
            return this.componentRef.comp.isEmpty;
        }
        isFocusable() {
            return this.componentRef.comp.isFocusable;
        }
        isSet() {
            if (this.componentRef.comp) {
                return this.componentRef.comp.isSet;
            }
            // because of the willStart, the real field widget may not be
            // instantiated yet when the renderer first asks if it is set
            // (only the wrapper is instantiated), so we instantiate one
            // with the same props, get its 'isSet' status, and destroy it.
            const c = new this.Component(null, this.props);
            const isSet = c.isSet;
            c.destroy();
            return isSet;
        }
        isValid() {
            return this.componentRef.comp.isValid;
        }
        reset(record, event) {
            return this.update({record, event});
        }
        setIDForLabel(id) {
            return this.componentRef.comp.setIDForLabel(id);
        }
        updateModifiersValue(modifiers) {
            return this.componentRef.comp.updateModifiersValue(modifiers);
        }
    }

    return FieldWrapper;
});
