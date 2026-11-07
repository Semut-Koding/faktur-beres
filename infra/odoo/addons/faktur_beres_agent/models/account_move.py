from odoo import models
from odoo.exceptions import AccessError

AGENT_GROUP = "faktur_beres_agent.group_agent"


class AccountMove(models.Model):
    _inherit = "account.move"

    def write(self, vals):
        """Refuse any change of ``state`` by a Faktur Beres agent user outside ``sudo``.

        Why this guard exists:

        * Odoo already refuses posting by users without ``account.group_account_invoice``
          (``account.move._post`` raises ``AccessError``), and the agent group never has that
          group or any group implying it.
        * The agent's record rules limit create and write to its own draft vendor bills, but
          record rules are evaluated on the records *before* the write. On its own draft the agent
          could therefore still change ``state`` through ``write``, for example ``button_cancel``
          writing ``state = 'cancel'``.

        This override is an extra layer on top of both: a write that carries ``state`` from a user
        of the agent group, outside ``sudo``, raises ``AccessError``. It adds no business logic;
        the agent itself runs in Python and talks to Odoo through the JSON-2 API (ADR-0004).
        """
        if "state" in vals and not self.env.su and self.env.user.has_group(AGENT_GROUP):
            raise AccessError(
                self.env._("User agent Faktur Beres tidak boleh mengubah status tagihan.")
            )
        return super().write(vals)
