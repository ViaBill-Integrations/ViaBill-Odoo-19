# ViaBill-Odoo-19
Odoo 19 payment provider integration for ViaBill Payments solution for e-commerce merchants. Supports authorize/capture transactions, PriceTag widgets, and merchant onboarding directly from the Odoo backend.

# ViaBill Payment Provider for Odoo 19

[![License: LGPL-3](https://img.shields.io/badge/License-LGPL%20v3-blue.svg)](https://www.gnu.org/licenses/lgpl-3.0)
[![Odoo Version](https://img.shields.io/badge/Odoo-19.0-purple)](https://www.odoo.com)

A fully integrated **ViaBill** payment provider module for Odoo 19, enabling Buy Now, Pay Later (BNPL) functionality for Nordic and European e-commerce stores.

## Features

- 🔐 **Merchant Onboarding** — Log in or register a ViaBill merchant account directly from the Odoo backend, with automatic retrieval of API Key, Secret Key, and PriceTag Script
- 💳 **Authorize & Capture** — Supports both immediate capture and separate authorize/capture transaction flows
- 🏷️ **PriceTag Widgets** — Display ViaBill installment previews on product, cart, and checkout pages
- 🌍 **Multi-country** — Configurable for DK, DE, ES, and other supported ViaBill markets
- 🧪 **Test Mode** — Full sandbox support for development and QA
- 🐛 **Debug Logging** — Built-in debug log viewer with last-50-entries display and one-click clear

## Requirements

- Odoo 19.0 (Community or Enterprise)
- A ViaBill merchant account ([sign up at viabill.com](https://www.viabill.com))
- Python 3.10+

## Installation and Configuration

Please read the companion User Guide in PDF format.