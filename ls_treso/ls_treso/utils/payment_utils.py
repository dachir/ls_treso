# Copyright (c) 2026, Kossivi Amouzou and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.utils import flt
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
from erpnext.accounts.party import get_party_account
from erpnext.setup.utils import get_exchange_rate


ACTIVE_MODES = ("Standalone", "ERPNext Integrated", "External Export")


_OPERATION_CONFIG = {
    "Encaissement": frappe._dict({
        "invoice_doctype": "Sales Invoice",
        "item_doctype": "Sales Invoice Item",
        "party_doctype": "Customer",
        "party_field": "customer",
        "tiers_type": "CLIENT",
        "detail_tiers_type": "Client",
        "payment_type": "Receive",
        "account_field": "income_account",
        "party_account_field": "debit_to",
    }),
    "Decaissement": frappe._dict({
        "invoice_doctype": "Purchase Invoice",
        "item_doctype": "Purchase Invoice Item",
        "party_doctype": "Supplier",
        "party_field": "supplier",
        "tiers_type": "FOURNISSEUR",
        "detail_tiers_type": "Fournisseur",
        "payment_type": "Pay",
        "account_field": "expense_account",
        "party_account_field": "credit_to",
    }),
}


def get_ls_treso_mode():
    return frappe.db.get_single_value("LS Treso Settings", "operating_mode")  or "Standalone"


def get_operation_config(doc_or_doctype):
    doctype = doc_or_doctype if isinstance(doc_or_doctype, str) else doc_or_doctype.doctype
    config = _OPERATION_CONFIG.get(doctype)
    if not config:
        frappe.throw(_("Type d'opération non supporté: {0}").format(doctype))
    return config


def _imputation_field(axis_type):
    try:
        index = int((axis_type or "").replace("Axe", "").strip())
    except ValueError:
        return None
    if index < 1 or index > 10:
        return None
    return "imputation_analytique" if index == 1 else f"imputation_analytique_{index}"


def _find_dimension_field(meta, dimension_doctype):
    if not meta or not dimension_doctype:
        return None

    # New model: Axe Analytique.correspondance contains the ERPNext dimension DocType.
    for df in meta.fields:
        if df.fieldtype == "Link" and df.options == dimension_doctype:
            return df.fieldname

    # Compatibility with old configurations where correspondence contained the label.
    for df in meta.fields:
        if df.label == dimension_doctype:
            return df.fieldname

    return None


def _get_accounting_dimension_field(dimension_doctype):
    """Return the ERPNext Accounting Dimension fieldname for a DocType, if configured."""
    if not dimension_doctype or not frappe.db.exists("DocType", "Accounting Dimension"):
        return None

    return frappe.db.get_value(
        "Accounting Dimension",
        {"document_type": dimension_doctype, "disabled": 0},
        "fieldname",
    )


def _get_invoice_nature(invoice):
    """Return a single LS Nature from custom_nature/dimension when unambiguous."""
    values = set()

    if invoice.meta.has_field("custom_nature") and invoice.get("custom_nature"):
        values.add(invoice.get("custom_nature"))

    dimension_field = _get_accounting_dimension_field("Nature Operations")
    if dimension_field and invoice.meta.has_field(dimension_field) and invoice.get(dimension_field):
        values.add(invoice.get(dimension_field))

    for item in invoice.get("items") or []:
        if item.meta.has_field("custom_nature") and item.get("custom_nature"):
            values.add(item.get("custom_nature"))
        if dimension_field and item.meta.has_field(dimension_field) and item.get(dimension_field):
            values.add(item.get(dimension_field))

    return next(iter(values)) if len(values) == 1 else None


def _set_invoice_nature(item, nature_name):
    """Always use custom_nature when available and also populate the configured dimension."""
    if item.meta.has_field("custom_nature"):
        item.set("custom_nature", nature_name)

    dimension_field = _get_accounting_dimension_field("Nature Operations")
    if dimension_field and item.meta.has_field(dimension_field):
        item.set(dimension_field, nature_name)


def _append_nature_description(item, nature):
    """Keep the Item description and append the LS Nature description/name."""
    nature_description = nature.get("description") or nature.nature or nature.name
    current = (item.description or "").strip()
    extra = (nature_description or "").strip()

    if extra and extra not in current:
        item.description = f"{current}\n{extra}" if current else extra


def _get_invoice_item_for_nature(nature):
    """Return the Item linked to the Nature, or the LS Tréso default Item."""
    if nature.item:
        return nature.item

    if nature.type_operation == "Encaissement":
        setting_field = "encaissement_item"
    elif nature.type_operation in ("Décaissement", "Decaissement"):
        setting_field = "decaissement_item"
    else:
        frappe.throw(
            _("Type d'opération non reconnu pour la nature {0}: {1}").format(
                nature.name, nature.type_operation
            )
        )

    item_code = frappe.db.get_single_value("LS Treso Settings", setting_field)
    if not item_code:
        frappe.throw(
            _("La nature {0} n'a pas d'Article et aucun Article par défaut n'est configuré dans LS Treso Settings ({1})").format(
                nature.name, setting_field
            )
        )

    return item_code


def _get_invoice_dimension_value(invoice, dimension_doctype):
    fieldname = _find_dimension_field(invoice.meta, dimension_doctype)
    if fieldname and invoice.get(fieldname):
        return invoice.get(fieldname)

    items_field = invoice.meta.get_field("items")
    if not items_field:
        return None

    item_meta = frappe.get_meta(items_field.options)
    item_fieldname = _find_dimension_field(item_meta, dimension_doctype)
    if not item_fieldname:
        return None

    values = {row.get(item_fieldname) for row in invoice.get("items") or [] if row.get(item_fieldname)}
    if len(values) == 1:
        return values.pop()

    # A Detail Operation can only contain one Section Analytique per axis.
    # If the invoice contains several values, the user keeps the possibility to fill it manually.
    return None


def _get_section_for_dimension(axis_name, dimension_value):
    if not axis_name or not dimension_value:
        return None

    return frappe.db.get_value(
        "Section Analytique",
        {"section": axis_name, "compte": dimension_value},
        "name",
    )


@frappe.whitelist()
def get_invoice_details(document_type, invoice, societe=None):
    """Return Tiers + LS analytical imputations from a Sales/Purchase Invoice."""
    if document_type not in ("Sales Invoice", "Purchase Invoice"):
        frappe.throw(_("Le document doit être une Sales Invoice ou une Purchase Invoice"))

    config = _OPERATION_CONFIG["Encaissement" if document_type == "Sales Invoice" else "Decaissement"]
    invoice_doc = frappe.get_doc(document_type, invoice)

    if invoice_doc.docstatus != 1:
        frappe.throw(_("La facture {0} doit être soumise").format(invoice))

    if societe and frappe.db.exists("Company", societe) and invoice_doc.company != societe:
        frappe.throw(_("La facture {0} appartient à la société {1}").format(invoice, invoice_doc.company))

    party = invoice_doc.get(config.party_field)
    tiers = frappe.db.get_value(
        "Tiers",
        {"code": party, "type": config.tiers_type},
        "name",
    )

    result = {
        "type_tiers": config.detail_tiers_type,
        "tiers": tiers or "",
    }

    nature = _get_invoice_nature(invoice_doc)
    if nature:
        result["nature_operations"] = nature

    axes = frappe.get_all(
        "Axe Analytique",
        filters={"type": ["in", [f"Axe {i}" for i in range(1, 11)]]},
        fields=["name", "type", "correspondance"],
        order_by="type asc",
    )

    for axis in axes:
        fieldname = _imputation_field(axis.type)
        if not fieldname or not axis.correspondance:
            continue

        dimension_value = _get_invoice_dimension_value(invoice_doc, axis.correspondance)
        section = _get_section_for_dimension(axis.name, dimension_value)
        if section:
            result[fieldname] = section

    return result


def _get_erpnext_party(tiers_name, config):
    if not tiers_name:
        frappe.throw(_("Veuillez renseigner le tiers"))

    tiers = frappe.get_doc("Tiers", tiers_name)
    if tiers.type != config.tiers_type:
        frappe.throw(
            _("Le tiers {0} doit être de type {1}").format(tiers_name, config.tiers_type)
        )

    party = tiers.code
    if not frappe.db.exists(config.party_doctype, party):
        frappe.throw(
            _("Le tiers {0} n'a pas de correspondance avec un {1} ERPNext").format(
                tiers_name, config.party_doctype
            )
        )
    return party


def _get_dimension_values_from_detail(row, item_doctype):
    values = {}
    item_meta = frappe.get_meta(item_doctype)

    for index in range(1, 11):
        fieldname = "imputation_analytique" if index == 1 else f"imputation_analytique_{index}"
        section_name = row.get(fieldname)
        if not section_name:
            continue

        section = frappe.get_doc("Section Analytique", section_name)
        axis = frappe.get_doc("Axe Analytique", section.section)
        dimension_field = _find_dimension_field(item_meta, axis.correspondance)
        if dimension_field:
            values[dimension_field] = section.compte

    return values


def _get_nature_flags(row):
    if not row.nature_operations:
        return frappe._dict()

    return frappe._dict(
        frappe.db.get_value(
            "Nature Operations",
            row.nature_operations,
            ["is_advance", "echange", "solde_initial", "compte_comptable"],
            as_dict=True,
        )
        or {}
    )


def get_special_operation(doc):
    """Return the unique special detail when operation is Exchange or Opening Balance."""
    special_rows = []

    for row in doc.details_operation_de_caisse or []:
        nature = _get_nature_flags(row)
        if nature.echange or nature.solde_initial:
            special_rows.append((row, nature))

    if not special_rows:
        return None

    if len(doc.details_operation_de_caisse or []) != 1:
        frappe.throw(
            _("Une opération d'échange ou de solde initial doit contenir une seule ligne de détail")
        )

    row, nature = special_rows[0]

    if nature.echange and nature.solde_initial:
        frappe.throw(_("Une Nature ne peut pas être à la fois Échange et Solde initial"))

    if row.invoice:
        frappe.throw(
            _("Une opération d'échange ou de solde initial ne doit pas être liée à une facture")
        )

    if doc.advance_allocation:
        frappe.throw(
            _("Une opération d'échange ou de solde initial ne peut pas utiliser d'avance")
        )

    if nature.solde_initial and doc.doctype != "Encaissement":
        frappe.throw(_("Une Nature Solde initial ne peut être utilisée que sur un Encaissement"))

    return frappe._dict({"row": row, "nature": nature})


def _set_payment_dimension(payment_entry, fieldname, value):
    if not fieldname or not value:
        return

    current_value = payment_entry.get(fieldname)
    if current_value and current_value != value:
        frappe.throw(
            _("La dimension {0} contient deux valeurs différentes: {1} et {2}").format(
                fieldname, current_value, value
            )
        )

    payment_entry.set(fieldname, value)


def _apply_special_dimensions(payment_entry, detail):
    """Copy Nature, analytical imputations and employee/tiers dimensions when representable."""
    pe_meta = frappe.get_meta("Payment Entry")

    nature_field = _find_dimension_field(pe_meta, "Nature Operations")
    if nature_field and detail.nature_operations:
        _set_payment_dimension(payment_entry, nature_field, detail.nature_operations)

    for fieldname, value in _get_dimension_values_from_detail(detail, "Payment Entry").items():
        if pe_meta.has_field(fieldname):
            _set_payment_dimension(payment_entry, fieldname, value)

    # For a cash transfer carrying an LS Tiers, copy it directly to the
    # Payment Entry fields when they exist. Internal Transfer keeps no party.
    if detail.tiers:
        tiers_doc = frappe.get_doc("Tiers", detail.tiers)

        if pe_meta.has_field("tiers"):
            _set_payment_dimension(payment_entry, "tiers", tiers_doc.name)

        if tiers_doc.type == "SALARIE" and tiers_doc.code and pe_meta.has_field("employee"):
            _set_payment_dimension(payment_entry, "employee", tiers_doc.code)


def _get_caisse_account(caisse):
    account = frappe.db.get_value("Caisse", caisse, "compte_comptable")
    if not account or not frappe.db.exists("Account", account):
        frappe.throw(_("Veuillez renseigner un Account ERPNext valide sur la caisse {0}").format(caisse))
    return account


def _get_party_account_currency(invoice, config):
    account = invoice.get(config.party_account_field)
    currency = frappe.db.get_value("Account", account, "account_currency") if account else None
    if not currency:
        frappe.throw(
            _("Impossible de déterminer la devise du compte tiers de la facture {0}").format(
                invoice.name
            )
        )
    return currency


def _get_ls_treso_exchange_rate(from_currency, to_currency, posting_date=None):
    """Return a multiplier converting from_currency to to_currency from LS Treso rates."""
    if not from_currency or not to_currency:
        return None
    if from_currency == to_currency:
        return 1.0

    date_filter = " AND date_cours <= %(posting_date)s" if posting_date else ""
    params = {
        "from_currency": from_currency,
        "to_currency": to_currency,
        "posting_date": posting_date,
    }

    # Cours Devise stores: parent = reference currency, devise = quoted currency,
    # cours = quoted/reference. Therefore quoted -> reference uses 1/cours.
    direct = frappe.db.sql(
        f"""
            SELECT cours
            FROM `tabCours Devise`
            WHERE parent = %(to_currency)s
              AND devise = %(from_currency)s
              {date_filter}
            ORDER BY date_cours DESC, creation DESC
            LIMIT 1
        """,
        params,
        as_dict=True,
    )
    if direct and flt(direct[0].cours):
        return 1 / flt(direct[0].cours)

    # Inverse quote: parent = from, devise = to, cours already means to/from.
    inverse = frappe.db.sql(
        f"""
            SELECT cours
            FROM `tabCours Devise`
            WHERE parent = %(from_currency)s
              AND devise = %(to_currency)s
              {date_filter}
            ORDER BY date_cours DESC, creation DESC
            LIMIT 1
        """,
        params,
        as_dict=True,
    )
    if inverse and flt(inverse[0].cours):
        return flt(inverse[0].cours)

    return None


def _get_currency_rate(doc, from_currency, to_currency, invoice=None, prefer_invoice=False):
    """Return multiplier from one currency to another.

    prefer_invoice=True is used for invoice allocations/outstanding values. Payment
    Entry account rates use the current LS Treso rate first, so real FX differences
    remain visible instead of being suppressed by the historical invoice rate.
    """
    if from_currency == to_currency:
        return 1.0

    def invoice_rate():
        if not invoice:
            return None
        company_currency = invoice.get("company_currency") or frappe.get_cached_value(
            "Company", invoice.company, "default_currency"
        )
        rate = flt(invoice.get("conversion_rate") or 1)
        if not rate:
            return None
        if from_currency == invoice.currency and to_currency == company_currency:
            return rate
        if from_currency == company_currency and to_currency == invoice.currency:
            return 1 / rate
        return None

    if prefer_invoice:
        rate = invoice_rate()
        if rate:
            return rate

    # The LS Treso operation carries the exact rate used by the cashier.
    if doc and flt(doc.cours):
        if from_currency == doc.devise and to_currency == doc.devise_caisse:
            return 1 / flt(doc.cours)
        if from_currency == doc.devise_caisse and to_currency == doc.devise:
            return flt(doc.cours)

    rate = _get_ls_treso_exchange_rate(from_currency, to_currency, getattr(doc, "date", None))
    if rate:
        return rate

    if not prefer_invoice:
        rate = invoice_rate()
        if rate:
            return rate

    # Last fallback to ERPNext's native exchange-rate resolver.
    rate = flt(get_exchange_rate(from_currency, to_currency, getattr(doc, "date", None)))
    return rate or None


def _convert_amount(doc, amount, from_currency, to_currency, invoice=None):
    amount = flt(amount)
    if from_currency == to_currency:
        return amount

    rate = _get_currency_rate(
        doc, from_currency, to_currency, invoice=invoice, prefer_invoice=bool(invoice)
    )
    if not rate:
        frappe.throw(
            _("Aucun taux de change disponible pour convertir {0} vers {1}").format(
                from_currency, to_currency
            )
        )
    return amount * rate


def _detail_amount_in_currency(doc, row, target_currency, invoice=None):
    """Return a detail amount in the currency expected by the party account."""
    if target_currency == doc.devise:
        return flt(row.montant_devise)

    if target_currency == doc.devise_caisse and flt(row.montant_devise_ref):
        return flt(row.montant_devise_ref)

    return _convert_amount(
        doc, flt(row.montant_devise), doc.devise, target_currency, invoice=invoice
    )


def _operation_amount_in_currency(doc, target_currency):
    if target_currency == doc.devise:
        return flt(doc.montant)

    if target_currency == doc.devise_caisse and flt(doc.montant_reference):
        return flt(doc.montant_reference)

    return _convert_amount(doc, flt(doc.montant), doc.devise, target_currency)


def _advance_amount_in_operation_currency(doc, row):
    """Advance Allocation.amount is in the selected Payment Entry party-account currency."""
    payment = frappe.get_doc("Payment Entry", row.payment_entry)
    payment_currency = (
        payment.paid_from_account_currency
        if payment.payment_type == "Receive"
        else payment.paid_to_account_currency
    )
    return _convert_amount(
        doc, flt(row.allocated_amount), payment_currency, doc.devise
    )


def _make_internal_transfer(
    source_account,
    target_account,
    paid_amount,
    received_amount,
    posting_date,
    reference_no,
    detail,
    remarks=None,
):
    source_company = frappe.db.get_value("Account", source_account, "company")
    target_company = frappe.db.get_value("Account", target_account, "company")

    if not source_company or source_company != target_company:
        frappe.throw(_("Les comptes source et destination doivent appartenir à la même Company"))

    if source_account == target_account:
        frappe.throw(_("Le compte source et le compte destination doivent être différents"))

    paid_amount = flt(paid_amount)
    received_amount = flt(received_amount)
    if paid_amount <= 0 or received_amount <= 0:
        frappe.throw(_("Les montants du transfert doivent être supérieurs à zéro"))

    company_currency = frappe.get_cached_value("Company", source_company, "default_currency")
    source_currency = frappe.db.get_value("Account", source_account, "account_currency")
    target_currency = frappe.db.get_value("Account", target_account, "account_currency")

    if not source_currency or not target_currency:
        frappe.throw(_("Impossible de déterminer la devise des comptes du transfert"))

    # The amounts entered in the transfer dialog are authoritative. Build rates
    # that make both sides of the Internal Transfer represent the same base
    # amount. This is especially important for USD -> CDF cash transfers.
    if source_currency == company_currency:
        source_rate = 1.0
        base_amount = paid_amount
        target_rate = base_amount / received_amount
    elif target_currency == company_currency:
        target_rate = 1.0
        base_amount = received_amount
        source_rate = base_amount / paid_amount
    else:
        source_rate = (
            _get_ls_treso_exchange_rate(source_currency, company_currency, posting_date)
            or flt(get_exchange_rate(source_currency, company_currency, posting_date))
        )
        if not source_rate:
            frappe.throw(
                _("Aucun taux de change disponible pour convertir {0} vers {1}").format(
                    source_currency, company_currency
                )
            )
        base_amount = paid_amount * source_rate
        target_rate = base_amount / received_amount

    pe = frappe.new_doc("Payment Entry")
    pe.payment_type = "Internal Transfer"
    pe.company = source_company
    pe.posting_date = posting_date
    pe.reference_no = reference_no
    pe.reference_date = posting_date
    pe.paid_from = source_account
    pe.paid_to = target_account
    pe.paid_from_account_currency = source_currency
    pe.paid_to_account_currency = target_currency
    pe.source_exchange_rate = source_rate
    pe.target_exchange_rate = target_rate
    pe.paid_amount = paid_amount
    pe.received_amount = received_amount
    pe.remarks = remarks or _("Transfert LS Tréso {0}").format(reference_no)

    _apply_special_dimensions(pe, detail)
    pe.submit()
    return pe


def make_internal_transfer_payment_entry(source_doc, target_doc=None):
    """Create the single ERPNext Internal Transfer for an LS Tréso cash transfer."""
    source_special = get_special_operation(source_doc)
    if not source_special or not source_special.nature.echange:
        frappe.throw(_("Le document source n'est pas une opération d'échange"))
    if source_doc.doctype != "Decaissement":
        frappe.throw(_("Le document source du transfert doit être un Décaissement"))

    source_account = _get_caisse_account(source_doc.caisse)

    if target_doc:
        target_special = get_special_operation(target_doc)
        if not target_special or not target_special.nature.echange:
            frappe.throw(_("Le document destination n'est pas une opération d'échange"))
        if target_doc.doctype != "Encaissement":
            frappe.throw(_("Le document destination du transfert doit être un Encaissement"))
        target_caisse = target_doc.caisse
        received_amount = flt(target_doc.montant)
    else:
        target_caisse = source_doc.remettant
        received_amount = flt(source_doc.montant_reference or source_doc.montant)

    if not target_caisse or not frappe.db.exists("Caisse", target_caisse):
        frappe.throw(_("La caisse destination du transfert est invalide"))

    target_account = _get_caisse_account(target_caisse)

    return _make_internal_transfer(
        source_account=source_account,
        target_account=target_account,
        paid_amount=source_doc.montant,
        received_amount=received_amount,
        posting_date=source_doc.date,
        reference_no=source_doc.name,
        detail=source_special.row,
        remarks=_("Transfert de caisse {0} vers {1}").format(source_doc.caisse, target_caisse),
    )


def _make_opening_balance_payment_entry(doc, special):
    source_account = special.nature.compte_comptable
    if not source_account or not frappe.db.exists("Account", source_account):
        frappe.throw(
            _("La Nature {0} doit avoir un Account ERPNext pour le solde initial").format(
                special.row.nature_operations
            )
        )

    target_account = _get_caisse_account(doc.caisse)
    amount = flt(doc.montant_reference or doc.montant)

    return _make_internal_transfer(
        source_account=source_account,
        target_account=target_account,
        paid_amount=amount,
        received_amount=amount,
        posting_date=doc.date,
        reference_no=doc.name,
        detail=special.row,
        remarks=_("Solde initial LS Tréso {0}").format(doc.name),
    )


def _get_default_uom():
    if frappe.db.exists("UOM", "Nos"):
        return "Nos"
    return frappe.db.get_value("UOM", {}, "name", order_by="creation asc")


def _get_company(doc, invoice_rows=None):
    if invoice_rows:
        return invoice_rows[0].invoice.company
    if frappe.db.exists("Company", doc.societe):
        return doc.societe

    caisse_account = frappe.db.get_value("Caisse", doc.caisse, "compte_comptable")
    company = frappe.db.get_value("Account", caisse_account, "company") if caisse_account else None
    if company:
        return company

    frappe.throw(
        _("Impossible de déterminer la Company ERPNext pour l'opération {0}").format(doc.name)
    )


def set_operation_totals(doc):
    advance_total = 0
    for row in doc.advance_allocation or []:
        if row.payment_entry and flt(row.allocated_amount):
            advance_total += _advance_amount_in_operation_currency(doc, row)

    doc.montant_avances_utilisees = advance_total
    doc.montant_total_operation = flt(doc.montant) + advance_total

    detail_total = sum(flt(d.montant_devise) for d in doc.details_operation_de_caisse)
    if flt(detail_total, 2) != flt(doc.montant_total_operation, 2):
        frappe.throw(
            _("Le total des détails {0} doit être égal au montant total de l'opération {1}").format(
                detail_total, doc.montant_total_operation
            )
        )


def create_missing_invoices(doc):
    """Create one invoice for all normal detail rows that do not already have one."""
    config = get_operation_config(doc)
    missing_rows = []
    parties = set()

    for row in doc.details_operation_de_caisse:
        nature = _get_nature_flags(row)

        if row.type_tiers == "Employe":
            if row.invoice:
                frappe.throw(
                    _("Ligne {0}: un employé ne peut pas être lié à une facture").format(row.idx)
                )
            continue

        row.document_type = config.invoice_doctype
        row.type_tiers = config.detail_tiers_type

        if nature.echange or nature.solde_initial:
            if row.invoice:
                frappe.throw(
                    _("Ligne {0}: une opération d'échange ou de solde initial ne peut pas être liée à une facture").format(
                        row.idx
                    )
                )
            continue

        if nature.is_advance:
            if row.invoice:
                frappe.throw(
                    _("Ligne {0}: une nature Avance ne peut pas être liée à une facture").format(
                        row.idx
                    )
                )

            if row.tiers:
                parties.add(_get_erpnext_party(row.tiers, config))

            continue

        if row.invoice:
            invoice = frappe.get_doc(config.invoice_doctype, row.invoice)

            if invoice.docstatus != 1:
                frappe.throw(
                    _("La facture {0} doit être soumise").format(row.invoice)
                )

            invoice_party = invoice.get(config.party_field)
            parties.add(invoice_party)

            if row.tiers and _get_erpnext_party(row.tiers, config) != invoice_party:
                frappe.throw(
                    _("Ligne {0}: le tiers ne correspond pas à la facture {1}").format(
                        row.idx,
                        row.invoice,
                    )
                )

            continue

        party = _get_erpnext_party(row.tiers, config)
        parties.add(party)
        missing_rows.append(row)

    if len(parties) > 1:
        frappe.throw(
            _("Une opération LS Tréso ne peut concerner qu'un seul tiers")
        )

    if not missing_rows:
        return

    party = next(iter(parties), None)

    if not party:
        frappe.throw(
            _("Veuillez renseigner le tiers pour créer la facture")
        )

    company = _get_company(doc)

    invoice = frappe.new_doc(config.invoice_doctype)
    invoice.company = company

    # Conserver exactement la date de l'opération LS Tréso.
    invoice.set_posting_time = 1
    invoice.posting_date = doc.date

    invoice.set(config.party_field, party)

    # Pour une Purchase Invoice générée automatiquement,
    # utiliser la même date pour posting / bill / due date.
    if config.invoice_doctype == "Purchase Invoice":
        invoice.bill_date = doc.date

    invoice.due_date = doc.date

    # La facture automatique est créée dans la devise de la transaction LS Tréso.
    if doc.get("devise") and frappe.db.exists("Currency", doc.devise):
        invoice.currency = doc.devise

    company_currency = frappe.get_cached_value(
        "Company",
        company,
        "default_currency",
    )

    if (
        invoice.currency
        and company_currency
        and invoice.currency != company_currency
    ):
        invoice.conversion_rate = _get_currency_rate(
            doc,
            invoice.currency,
            company_currency,
        )

        if not flt(invoice.conversion_rate):
            frappe.throw(
                _(
                    "Aucun taux de change disponible pour créer la facture en {0} "
                    "(Company: {1})"
                ).format(
                    invoice.currency,
                    company_currency,
                )
            )

    parent_dimension_values = {}
    row_item_pairs = []
    nature_values = set()

    # Chaque ligne LS Tréso devient une ligne de facture.
    for row in missing_rows:
        nature = frappe.get_doc(
            "Nature Operations",
            row.nature_operations,
        )

        item_code = _get_invoice_item_for_nature(nature)

        item = invoice.append(
            "items",
            {
                "item_code": item_code,
                "qty": 1,
                "rate": flt(row.montant_devise),
            },
        )

        row_item_pairs.append(
            (row, nature, item)
        )

        nature_values.add(
            row.nature_operations
        )

    # Laisser ERPNext appliquer les valeurs par défaut de l'Item.
    invoice.set_missing_values()

    for row, nature, item in row_item_pairs:

        # Le montant LS Tréso reste prioritaire.
        item.qty = 1
        item.rate = flt(row.montant_devise)

        # Description Item + description Nature.
        _append_nature_description(
            item,
            nature,
        )

        # custom_nature + éventuelle dimension Nature.
        _set_invoice_nature(
            item,
            row.nature_operations,
        )

        # Dimensions analytiques.
        dimensions = _get_dimension_values_from_detail(
            row,
            config.item_doctype,
        )

        for fieldname, value in dimensions.items():
            item.set(
                fieldname,
                value,
            )

            parent_dimension_values.setdefault(
                fieldname,
                set(),
            ).add(value)

    # Compatibilité custom_nature au niveau entête
    # uniquement si toutes les lignes ont la même Nature.
    invoice_meta = frappe.get_meta(
        config.invoice_doctype
    )

    item_meta = frappe.get_meta(
        config.item_doctype
    )

    if len(nature_values) == 1:
        nature_name = next(
            iter(nature_values)
        )

        if invoice_meta.has_field(
            "custom_nature"
        ):
            invoice.set(
                "custom_nature",
                nature_name,
            )

        dimension_field = _get_accounting_dimension_field(
            "Nature Operations"
        )

        if (
            dimension_field
            and invoice_meta.has_field(
                dimension_field
            )
        ):
            invoice.set(
                dimension_field,
                nature_name,
            )

    # Mettre une dimension sur l'entête seulement si toutes
    # les lignes portent exactement la même valeur.
    for item_fieldname, values in parent_dimension_values.items():

        if len(values) != 1:
            continue

        item_df = item_meta.get_field(
            item_fieldname
        )

        if not item_df:
            continue

        parent_fieldname = _find_dimension_field(
            invoice_meta,
            item_df.options,
        )

        if parent_fieldname:
            invoice.set(
                parent_fieldname,
                next(iter(values)),
            )

    # Sales Invoice Item exige un Cost Center.
    default_cost_center = frappe.db.get_value(
        "Company",
        company,
        "cost_center",
    )

    if default_cost_center:

        for item in invoice.items:

            if (
                item.meta.get_field("cost_center")
                and not item.cost_center
            ):
                item.cost_center = default_cost_center

    invoice.remarks = _(
        "Créée automatiquement depuis {0} {1}"
    ).format(
        doc.doctype,
        doc.name,
    )

    #
    # IMPORTANT :
    # aucune transaction n'est commit ici.
    # La facture et le Payment Entry restent dans
    # la transaction du submit LS Tréso.
    #
    invoice.insert()
    invoice.submit()

    #
    # Retenir uniquement dans la transaction courante
    # que cette facture vient d'être générée par LS Tréso.
    #
    doc.flags.ls_treso_auto_invoice = invoice.name

    for row in missing_rows:
        row.invoice = invoice.name
        row.document_type = config.invoice_doctype

    return invoice


def get_invoice_rows(doc, validate_outstanding=True):
    config = get_operation_config(doc)
    rows_by_invoice = {}
    order = []
    advance_rows = []

    for row in doc.details_operation_de_caisse:
        nature = _get_nature_flags(row)

        if row.type_tiers == "Employe":
            if row.invoice:
                frappe.throw(
                    _("Ligne {0}: un employé ne peut pas être lié à une facture").format(row.idx)
                )
            continue

        if nature.echange or nature.solde_initial:
            if row.invoice:
                frappe.throw(
                    _("Ligne {0}: une opération d'échange ou de solde initial ne peut pas être liée à une facture").format(row.idx)
                )
            continue

        if nature.is_advance:
            if row.invoice:
                frappe.throw(
                    _("Ligne {0}: une nature Avance ne peut pas être liée à une facture").format(row.idx)
                )
            advance_rows.append(row)
            continue

        if row.document_type != config.invoice_doctype:
            frappe.throw(
                _("Ligne {0}: cette opération ne peut contenir que des {1}").format(
                    row.idx, config.invoice_doctype
                )
            )
        if not row.invoice:
            frappe.throw(_("Ligne {0}: aucune facture n'a pu être rattachée").format(row.idx))

        if row.invoice not in rows_by_invoice:
            rows_by_invoice[row.invoice] = []
            order.append(row.invoice)
        rows_by_invoice[row.invoice].append(row)

    invoice_rows = []
    party = None
    company = None
    party_currency = None

    for name in order:
        invoice = frappe.get_doc(config.invoice_doctype, name)
        if invoice.docstatus != 1:
            frappe.throw(_("La facture {0} doit être soumise").format(name))

        invoice_party = invoice.get(config.party_field)
        invoice_party_currency = _get_party_account_currency(invoice, config)

        if party and invoice_party != party:
            frappe.throw(_("Toutes les factures doivent appartenir au même tiers"))
        if company and invoice.company != company:
            frappe.throw(_("Toutes les factures doivent appartenir à la même société"))
        if party_currency and invoice_party_currency != party_currency:
            frappe.throw(_("Toutes les factures doivent utiliser le même compte tiers / devise"))

        party = party or invoice_party
        company = company or invoice.company
        party_currency = party_currency or invoice_party_currency

        # allocated_amount settles the invoice and must therefore use the
        # invoice/reference rate. The cash side keeps the current LS Tréso rate
        # below, allowing ERPNext to calculate the real exchange difference.
        amount = sum(
            _convert_amount(
                doc, flt(row.montant_devise), doc.devise, party_currency, invoice=invoice
            )
            for row in rows_by_invoice[name]
        )

        # Do not reproduce ERPNext's outstanding validation here. Payment Entry
        # recalculates the current outstanding in the party-account currency at submit time.
        invoice_rows.append(
            frappe._dict(
                {
                    "name": name,
                    "amount": amount,
                    "invoice": invoice,
                }
            )
        )

    # An operation containing only a new advance has no invoice from which to infer the party currency.
    if not party and advance_rows:
        detail_tiers = next((row.tiers for row in advance_rows if row.tiers), None)
        party = _get_erpnext_party(detail_tiers, config)
        company = _get_company(doc)
        party_account = get_party_account(config.party_doctype, party, company)
        party_currency = frappe.db.get_value("Account", party_account, "account_currency")

    new_advance_amount = sum(
        _detail_amount_in_currency(doc, row, party_currency)
        for row in advance_rows
    ) if advance_rows else 0

    return invoice_rows, new_advance_amount, party, company, party_currency


def get_advance_distribution(doc, invoice_rows, party=None, company=None, check_available=True):
    config = get_operation_config(doc)
    remaining_by_invoice = {d.name: flt(d.amount) for d in invoice_rows}
    distribution = []
    seen = set()

    for row in doc.advance_allocation or []:
        if not row.payment_entry or flt(row.allocated_amount) <= 0:
            continue
        if row.payment_entry in seen:
            frappe.throw(
                _("Le Payment Entry {0} ne doit apparaître qu'une seule fois dans les avances").format(
                    row.payment_entry
                )
            )
        seen.add(row.payment_entry)

        payment = frappe.get_doc("Payment Entry", row.payment_entry)
        if (
            payment.docstatus != 1
            or payment.payment_type != config.payment_type
            or payment.party_type != config.party_doctype
        ):
            frappe.throw(_("Le Payment Entry {0} n'est pas une avance valide").format(row.payment_entry))
        if party and payment.party != party:
            frappe.throw(_("Le Payment Entry {0} appartient à un autre tiers").format(row.payment_entry))
        if company and payment.company != company:
            frappe.throw(_("Le Payment Entry {0} appartient à une autre société").format(row.payment_entry))

        available = flt(payment.unallocated_amount)
        row.available_amount = available
        if check_available and flt(row.allocated_amount, 2) > flt(available, 2):
            frappe.throw(
                _("Le montant alloué sur {0} dépasse le montant disponible {1}").format(
                    row.payment_entry, available
                )
            )

        remaining = flt(row.allocated_amount)
        allocations = []
        for invoice in invoice_rows:
            if remaining <= 0:
                break
            available_on_invoice = flt(remaining_by_invoice.get(invoice.name))
            if available_on_invoice <= 0:
                continue
            amount = min(remaining, available_on_invoice)
            allocations.append(frappe._dict({"invoice": invoice.name, "amount": amount}))
            remaining_by_invoice[invoice.name] = available_on_invoice - amount
            remaining -= amount

        if flt(remaining, 2) > 0:
            frappe.throw(_("Le montant des avances dépasse le montant des factures à régler"))

        distribution.append(
            frappe._dict({"row": row, "payment": payment, "allocations": allocations})
        )

    return distribution


def prepare_operation(doc):
    set_operation_totals(doc)
    if get_special_operation(doc):
        return
    create_missing_invoices(doc)
    get_invoice_rows(doc)


def _make_auto_invoice_payment_entry(doc, invoice_name, config, caisse_account):
    """
    Create the Payment Entry for an invoice automatically generated by LS Tréso.

    Same rule for:
        Encaissement -> Sales Invoice -> Receive
        Decaissement -> Purchase Invoice -> Pay

    The invoice has already been submitted in the transaction currency.
    ERPNext determines the outstanding in the party-account currency.
    """

    invoice = frappe.get_doc(
        config.invoice_doctype,
        invoice_name,
    )

    if invoice.docstatus != 1:
        frappe.throw(
            _("La facture {0} doit être soumise").format(invoice_name)
        )

    caisse_currency = frappe.db.get_value(
        "Account",
        caisse_account,
        "account_currency",
    )

    bank_amount = _operation_amount_in_currency(
        doc,
        caisse_currency,
    )

    # Let ERPNext build the Payment Entry from the submitted invoice.
    pe = get_payment_entry(
        config.invoice_doctype,
        invoice_name,
        bank_account=caisse_account,
        bank_amount=bank_amount,
        payment_type=config.payment_type,
        reference_date=doc.date,
    )

    pe.posting_date = doc.date
    pe.reference_no = doc.name
    pe.reference_date = doc.date
    pe.remarks = _(
        "{0} LS Tréso {1}"
    ).format(
        doc.doctype,
        doc.name,
    )

    # Important:
    # refresh the reference using ERPNext's own outstanding calculation.
    pe.set_missing_ref_details(force=True)

    references = [
        ref
        for ref in pe.references
        if ref.reference_doctype == config.invoice_doctype
        and ref.reference_name == invoice_name
    ]

    if not references:
        frappe.throw(
            _("Aucune référence de paiement n'a été générée pour la facture {0}").format(
                invoice_name
            )
        )

    # The automatically generated invoice is fully paid by this operation.
    # Do not reconvert the LS amount here:
    # use the outstanding calculated by ERPNext.
    party_amount = 0

    for ref in references:
        ref.allocated_amount = flt(ref.outstanding_amount)
        party_amount += flt(ref.allocated_amount)

    if party_amount <= 0:
        frappe.throw(
            _("La facture {0} n'a aucun montant restant à régler").format(
                invoice_name
            )
        )

    # party_amount is in party-account currency.
    # bank_amount is in cash-account currency.
    if config.payment_type == "Receive":
        pe.paid_amount = party_amount
        pe.received_amount = bank_amount
    else:
        pe.paid_amount = bank_amount
        pe.received_amount = party_amount

    pe.submit()

    return pe


def make_payment_entry(doc):
    if doc.flags.get("skip_ls_treso_payment_entry"):
        return

    if flt(doc.montant) <= 0:
        return

    special = get_special_operation(doc)

    if special:
        if special.nature.echange:
            # Le transfert de caisse crée un seul Payment Entry,
            # depuis le Décaissement.
            if doc.doctype == "Decaissement":
                return make_internal_transfer_payment_entry(doc)

            return

        if special.nature.solde_initial:
            return _make_opening_balance_payment_entry(
                doc,
                special,
            )

    config = get_operation_config(doc)
    caisse_account = _get_caisse_account(doc.caisse)

    # ==========================================================
    # FACTURE AUTOMATIQUEMENT CRÉÉE PAR LS TRÉSO
    # ==========================================================

    auto_invoice = doc.flags.get(
        "ls_treso_auto_invoice"
    )

    if auto_invoice and not doc.advance_allocation:

        auto_invoice_only = True
        has_normal_row = False

        for row in doc.details_operation_de_caisse:

            nature = _get_nature_flags(row)

            # Employé : pas de facture.
            if row.type_tiers == "Employe":
                auto_invoice_only = False
                break

            # Opérations spéciales : autre traitement.
            if nature.echange or nature.solde_initial:
                auto_invoice_only = False
                break

            # Nouvelle avance : conserver le flux général.
            if nature.is_advance:
                auto_invoice_only = False
                break

            has_normal_row = True

            # Toutes les lignes normales doivent appartenir
            # à la facture qui vient d'être créée.
            if row.invoice != auto_invoice:
                auto_invoice_only = False
                break

        if has_normal_row and auto_invoice_only:
            return _make_auto_invoice_payment_entry(
                doc,
                auto_invoice,
                config,
                caisse_account,
            )

    # ==========================================================
    # FLUX GÉNÉRAL EXISTANT
    # Factures existantes / plusieurs factures / avances /
    # Employee, etc.
    # ==========================================================

    employee_row = next(
        (
            row
            for row in doc.details_operation_de_caisse
            if row.type_tiers == "Employe"
        ),
        None,
    )

    if employee_row:

        invoice_rows = []
        new_advance_amount = 0

        party = employee_row.tiers
        company = _get_company(doc)

        party_type = "Employee"

        party_account = get_party_account(
            party_type,
            party,
            company,
        )

        party_currency = frappe.db.get_value(
            "Account",
            party_account,
            "account_currency",
        )

    else:

        (
            invoice_rows,
            new_advance_amount,
            party,
            company,
            party_currency,
        ) = get_invoice_rows(doc)

        party_type = config.party_doctype

    distribution = get_advance_distribution(
        doc,
        invoice_rows,
        party,
        company,
    )

    advance_by_invoice = {}

    for advance in distribution:

        for allocation in advance.allocations:

            advance_by_invoice[
                allocation.invoice
            ] = (
                flt(
                    advance_by_invoice.get(
                        allocation.invoice
                    )
                )
                + flt(allocation.amount)
            )

    allocations = {}

    for invoice in invoice_rows:

        amount = (
            flt(invoice.amount)
            - flt(
                advance_by_invoice.get(
                    invoice.name
                )
            )
        )

        if amount > 0:
            allocations[
                invoice.name
            ] = amount

    current_allocated = sum(
        allocations.values()
    )

    expected_party_amount = _operation_amount_in_currency(
        doc,
        party_currency,
    )

    party_amount = (
        expected_party_amount
        if employee_row
        else flt(
            current_allocated
            + new_advance_amount
        )
    )

    if (
        abs(
            flt(party_amount)
            - flt(expected_party_amount)
        )
        > 0.01
    ):
        frappe.throw(
            _(
                "Le montant courant ne correspond pas "
                "aux factures et aux nouvelles avances"
            )
        )

    caisse_currency = frappe.db.get_value(
        "Account",
        caisse_account,
        "account_currency",
    )

    bank_amount = _operation_amount_in_currency(
        doc,
        caisse_currency,
    )

    if invoice_rows:

        pe = get_payment_entry(
            config.invoice_doctype,
            invoice_rows[0].name,
            party_amount=party_amount,
            bank_account=caisse_account,
            bank_amount=bank_amount,
            payment_type=config.payment_type,
            reference_date=doc.date,
        )

        # Plusieurs références peuvent être reconstruites
        # dans le flux général.
        pe.set(
            "references",
            [],
        )

    else:

        if not employee_row:

            detail_tiers = next(
                (
                    d.tiers
                    for d in doc.details_operation_de_caisse
                    if d.tiers
                ),
                None,
            )

            party = _get_erpnext_party(
                detail_tiers,
                config,
            )

            company = _get_company(doc)

            party_account = get_party_account(
                party_type,
                party,
                company,
            )

            party_currency = frappe.db.get_value(
                "Account",
                party_account,
                "account_currency",
            )

        party_amount = _operation_amount_in_currency(
            doc,
            party_currency,
        )

        bank_amount = _operation_amount_in_currency(
            doc,
            caisse_currency,
        )

        pe = frappe.new_doc(
            "Payment Entry"
        )

        pe.payment_type = config.payment_type
        pe.company = company
        pe.party_type = party_type
        pe.party = party

        if config.payment_type == "Receive":

            pe.paid_from = get_party_account(
                party_type,
                party,
                company,
            )

            pe.paid_to = caisse_account

        else:

            pe.paid_from = caisse_account

            pe.paid_to = get_party_account(
                party_type,
                party,
                company,
            )

    # Reconstruction des références uniquement
    # pour le flux général.
    for invoice_name, amount in allocations.items():

        pe.append(
            "references",
            {
                "reference_doctype": config.invoice_doctype,
                "reference_name": invoice_name,
                "allocated_amount": amount,
            },
        )

    pe.posting_date = doc.date
    pe.reference_no = doc.name
    pe.reference_date = doc.date

    pe.remarks = _(
        "{0} LS Tréso {1}"
    ).format(
        doc.doctype,
        doc.name,
    )

    company_currency = frappe.get_cached_value(
        "Company",
        pe.company,
        "default_currency",
    )

    pe.paid_from_account_currency = (
        pe.paid_from_account_currency
        or frappe.db.get_value(
            "Account",
            pe.paid_from,
            "account_currency",
        )
    )

    pe.paid_to_account_currency = (
        pe.paid_to_account_currency
        or frappe.db.get_value(
            "Account",
            pe.paid_to,
            "account_currency",
        )
    )

    reference_invoice = (
        invoice_rows[0].invoice
        if len(invoice_rows) == 1
        else None
    )

    pe.source_exchange_rate = _get_currency_rate(
        doc,
        pe.paid_from_account_currency,
        company_currency,
        invoice=reference_invoice,
    )

    pe.target_exchange_rate = _get_currency_rate(
        doc,
        pe.paid_to_account_currency,
        company_currency,
        invoice=reference_invoice,
    )

    if config.payment_type == "Receive":

        pe.paid_amount = party_amount
        pe.received_amount = bank_amount

    else:

        pe.paid_amount = bank_amount
        pe.received_amount = party_amount

    pe.submit()

    return pe


def reconcile_advances(doc):
    if not doc.advance_allocation or get_special_operation(doc):
        return

    config = get_operation_config(doc)
    invoice_rows, _, party, company, _ = get_invoice_rows(doc, validate_outstanding=False)
    distribution = get_advance_distribution(doc, invoice_rows, party, company)

    for advance in distribution:
        if not advance.allocations:
            continue

        payment_doc = advance.payment
        recon = frappe.new_doc("Payment Reconciliation")
        recon.company = payment_doc.company
        recon.party_type = config.party_doctype
        recon.party = payment_doc.party
        recon.receivable_payable_account = (
            payment_doc.paid_from if config.payment_type == "Receive" else payment_doc.paid_to
        )
        recon.payment_name = payment_doc.name
        recon.get_unreconciled_entries()

        payment = next(
            (
                d
                for d in recon.payments
                if d.reference_type == "Payment Entry" and d.reference_name == payment_doc.name
            ),
            None,
        )
        if not payment:
            frappe.throw(_("Le Payment Entry {0} n'a plus de montant disponible").format(payment_doc.name))

        allocated_total = sum(flt(d.amount) for d in advance.allocations)
        if flt(allocated_total, 2) > flt(payment.amount, 2):
            frappe.throw(
                _("Le montant alloué dépasse le montant disponible du Payment Entry {0}").format(
                    payment_doc.name
                )
            )

        payment.unreconciled_amount = flt(payment.amount)
        exchange_map = recon.get_invoice_exchange_map(recon.invoices, [payment])
        remaining_payment = flt(payment.amount)

        for allocation in advance.allocations:
            if frappe.db.exists(
                "Payment Entry Reference",
                {
                    "parent": payment_doc.name,
                    "reference_doctype": config.invoice_doctype,
                    "reference_name": allocation.invoice,
                    "docstatus": 1,
                },
            ):
                frappe.throw(
                    _("Le Payment Entry {0} est déjà rapproché avec {1}").format(
                        payment_doc.name, allocation.invoice
                    )
                )

            invoice = next(
                (
                    d
                    for d in recon.invoices
                    if d.invoice_type == config.invoice_doctype
                    and d.invoice_number == allocation.invoice
                ),
                None,
            )
            if not invoice or flt(allocation.amount, 2) > flt(invoice.outstanding_amount, 2):
                frappe.throw(
                    _("Impossible de rapprocher {0} avec la facture {1}").format(
                        payment_doc.name, allocation.invoice
                    )
                )

            payment.amount = remaining_payment
            invoice.exchange_rate = exchange_map.get(invoice.invoice_number)
            row = recon.get_allocated_entry(payment, invoice, flt(allocation.amount))
            row.unreconciled_amount = payment.unreconciled_amount
            row.difference_amount = recon.get_difference_amount(payment, invoice, flt(allocation.amount))
            row.difference_account = frappe.db.get_value(
                "Company", payment_doc.company, "exchange_gain_loss_account"
            )
            row.exchange_rate = invoice.exchange_rate
            row.gain_loss_posting_date = doc.date
            recon.append("allocation", row)
            remaining_payment -= flt(allocation.amount)

        recon.reconcile()


def unreconcile_advances(doc):
    if not doc.advance_allocation:
        return

    from erpnext.accounts.doctype.unreconcile_payment.unreconcile_payment import (
        create_unreconcile_doc_for_selection,
    )

    config = get_operation_config(doc)
    invoice_rows, _, party, company, _ = get_invoice_rows(doc, validate_outstanding=False)
    distribution = get_advance_distribution(
        doc, invoice_rows, party, company, check_available=False
    )
    selections = []

    for advance in distribution:
        for allocation in advance.allocations:
            selections.append(
                {
                    "company": advance.payment.company,
                    "voucher_type": "Payment Entry",
                    "voucher_no": advance.payment.name,
                    "against_voucher_type": config.invoice_doctype,
                    "against_voucher_no": allocation.invoice,
                }
            )

    if selections:
        create_unreconcile_doc_for_selection(json.dumps(selections))


def cancel_payment_entry(doc):
    name = frappe.db.get_value(
        "Payment Entry", {"reference_no": doc.name, "docstatus": 1}, "name"
    )
    if name:
        frappe.get_doc("Payment Entry", name).cancel()
