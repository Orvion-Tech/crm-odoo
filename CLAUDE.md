# CRM Custom Module for Odoo

## About This Project
Custom CRM module for a **Smart Lock selling company** built in Odoo.
Developer is a Django developer learning Odoo — explain Odoo concepts in Django terms when helpful.

## Company Context
- Company sells smart locks: biometric door locks, smart padlocks, keypad locks, access control systems, accessories
- Customers: builders, architects, hotels, offices, residential buyers, dealers
- Sales flow: Inquiry → Lead → Quotation → Sales Order → Invoice + Delivery

## Module: crm_custom
Technical name: `crm_custom`
Location: `custom_addons/crm_custom/`

## Business Flow
```
Customer Inquiry (INQ/0001)
    ↓ Qualify
Lead (LEAD/0001) — Kanban stages: New → Qualified → Proposal → Negotiation → Won/Lost
    ↓ Create Quotation
Quotation (QTN/0001) — with product lines, terms, validity date
    ↓ Confirm
Sales Order (SO/0001) — confirmed order, delivery tracking
    ↓ Create Invoice
Invoice (INV/0001) — billing, payment status
    ↓ Deliver
Inventory — product stock in/out, warehouse tracking
```

## Models to Build
1. `crm.inquiry` + `crm.inquiry.line` — Customer inquiries (DONE - basic version)
2. `crm.lead` — Leads with kanban stages and scoring
3. `crm.quotation` + `crm.quotation.line` — Formal quotations
4. `crm.sale.order` + `crm.sale.order.line` — Confirmed orders
5. `crm.invoice` + `crm.invoice.line` — Invoices and payments
6. `crm.product` — Smart lock products catalog
7. `crm.product.category` — Product categories
8. `crm.inventory` — Stock tracking
9. `crm.warehouse` — Warehouse locations

## Sample Products
- Smart Door Lock Pro X1 (Biometric + Keypad) — ₹12,500
- Smart Padlock S200 (Bluetooth) — ₹3,500
- Access Control Panel ACP-400 — ₹28,000
- Smart Lock Lite L100 (Keypad only) — ₹6,800
- Biometric Gate Lock BGL-700 — ₹18,500
- Smart Lock Accessories Kit — ₹1,200

## Product Categories
- Biometric Locks
- Keypad Locks
- Bluetooth/WiFi Locks
- Access Control Systems
- Accessories & Parts

## Coding Guidelines
- Always add security rules in ir.model.access.csv for new models
- Always add sequences for auto-numbering (INQ/, LEAD/, QTN/, SO/, INV/)
- Use `tracking=True` on important fields for audit trail
- Use statusbar widget for state fields
- Add proper `_order` on all models
- Keep all views in separate XML files per model (inquiry_views.xml, lead_views.xml, etc.)
- Use `@api.depends` for computed fields, `@api.onchange` for UI-only changes
- Raise `UserError` for business validation errors
- Use `fields.Html` for notes/description fields
- Add `copy=False` on reference/sequence fields

## Current File Structure
```
crm_custom/
├── __init__.py
├── __manifest__.py
├── CLAUDE.md              ← this file
├── models/
│   ├── __init__.py
│   └── inquiry.py
├── security/
│   └── ir.model.access.csv
├── data/
│   └── sequence.xml
├── views/
│   └── inquiry_views.xml
└── static/
    └── description/
        └── icon.png
```

## Odoo Version
Odoo 19 Community Edition (confirm with developer)

## Important Reminders
- After adding new model files, update models/__init__.py
- After adding new XML files, update __manifest__.py data list
- Load security CSV BEFORE view XML files in manifest
- Restart Odoo and upgrade module after changes: ./odoo-bin -u crm_custom -d <dbname>