# ViaBill Payment Provider — Odoo 19.0

A fully integrated Odoo 19.0 payment provider module for **ViaBill**, a buy-now-pay-later (BNPL) payment solution. This module implements the ViaBill checkout redirect flow, server-to-server callback processing, manual capture, partial refunds, and void/cancel operations.

# Installation and Configuration

Please read the User Guide (PDF file).

## ViaBill Account Credentials

The module does not contact the ViaBill server to register or log in. Instead, enter your **API Key**, **Secret Key** and **PriceTag Script** manually in the *Credentials* section of the ViaBill payment provider form (Accounting/Website → Configuration → Payment Providers → ViaBill). You can find these values in your ViaBill merchant account.

- The Secret Key is displayed as a masked password field.
- For the PriceTag Script, paste the full `<script>…</script>` snippet as provided by ViaBill (raw inline JavaScript without the tags is also accepted).
- The ViaBill payment option is not offered at checkout until the API Key and Secret Key have been saved.