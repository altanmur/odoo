
odoo.define('mail.form_controller', function (require) {
"use strict";

var FormController = require('web.FormController');

/**
 * Include the FormController to update the datapoint on the model when a
 * message is posted.
 */
FormController.include({
    custom_events: _.extend({}, FormController.prototype.custom_events, {
        new_message: '_onNewMessage',
    }),

    //--------------------------------------------------------------------------
    // Public
    //--------------------------------------------------------------------------

    /**
     * @override
     */
    async saveRecord() {
        const changedFields = await this._super(...arguments);
        const chatter = this.renderer.chatter;
        if (chatter) {
            await chatter.updateSuggestedPartners();
        }
        return changedFields;
    },

    //--------------------------------------------------------------------------
    // Handlers
    //--------------------------------------------------------------------------

    /**
     * @private
     * @param {OdooEvent} ev
     * @param {string} event.data.id datapointID
     * @param {integer[]} event.data.messageIDs list of message IDs
     */
    _onNewMessage: function (ev) {
        this.model.updateMessageIDs(ev.data.id, ev.data.messageIDs);
    },
});

});
