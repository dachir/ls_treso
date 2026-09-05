import json

import frappe
from frappe import _
from frappe.utils import flt
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry, get_reference_details
from erpnext.accounts.doctype.unreconcile_payment.unreconcile_payment import create_unreconcile_doc_for_selection
from erpnext.accounts.party import get_party_account
from erpnext.setup.utils import get_exchange_rate


CONFIG = {
    "Encaissement": frappe._dict(invoice_doctype="Sales Invoice", item_doctype="Sales Invoice Item",
        party_doctype="Customer", party_field="customer", party_account_field="debit_to",
        tiers_type="CLIENT", detail_tiers_type="Client", payment_type="Receive",
        default_item="encaissement_item"),
    "Decaissement": frappe._dict(invoice_doctype="Purchase Invoice", item_doctype="Purchase Invoice Item",
        party_doctype="Supplier", party_field="supplier", party_account_field="credit_to",
        tiers_type="FOURNISSEUR", detail_tiers_type="Fournisseur", payment_type="Pay",
        default_item="decaissement_item"),
}


def cfg(doc):
    if doc.doctype not in CONFIG:
        frappe.throw(_("Type d'opération non supporté: {0}").format(doc.doctype))
    return CONFIG[doc.doctype]


def caisse_account(doc):
    account = frappe.db.get_value("Caisse", doc.caisse, "compte_comptable")
    if not account or not frappe.db.exists("Account", account):
        frappe.throw(_("Compte ERPNext invalide pour la caisse {0}").format(doc.caisse))
    return account


def company(doc):
    return doc.societe if frappe.db.exists("Company", doc.societe) else frappe.db.get_value(
        "Account", caisse_account(doc), "company"
    )


def nature(row):
    if not row.nature_operations:
        frappe.throw(_("Ligne {0}: Nature Operations obligatoire").format(row.idx))
    return frappe.get_doc("Nature Operations", row.nature_operations)


def party(tiers_name, config):
    tiers = frappe.get_doc("Tiers", tiers_name)
    if tiers.type != config.tiers_type or not frappe.db.exists(config.party_doctype, tiers.code):
        frappe.throw(_("Tiers ERPNext invalide: {0}").format(tiers_name))
    return tiers.code


def employee(tiers_name):
    if frappe.db.exists("Employee", tiers_name):
        return tiers_name
    code = frappe.db.get_value("Tiers", tiers_name, "code")
    if code and frappe.db.exists("Employee", code):
        return code
    frappe.throw(_("Correspondance Employee absente pour {0}").format(tiers_name))


def convert(amount, source, target, date):
    if source == target:
        return flt(amount)
    rate = flt(get_exchange_rate(source, target, date))
    if not rate:
        frappe.throw(_("Taux ERPNext absent: {0} -> {1}").format(source, target))
    return flt(amount) * rate


def invoice_to_party(invoice, amount, party_currency):
    if invoice.currency == party_currency:
        return flt(amount)
    base = frappe.get_cached_value("Company", invoice.company, "default_currency")
    if party_currency == base and flt(invoice.conversion_rate):
        return flt(amount) * flt(invoice.conversion_rate)
    return convert(amount, invoice.currency, party_currency, invoice.posting_date)


def operation_amount(doc, account):
    currency = frappe.db.get_value("Account", account, "account_currency")
    if currency == doc.devise:
        return flt(doc.montant)
    if currency == doc.devise_caisse and flt(doc.montant_reference):
        return flt(doc.montant_reference)
    return convert(doc.montant, doc.devise, currency, doc.date)


class DimensionManager:
    def __init__(self, doc):
        self.doc, self.config = doc, cfg(doc)

    def validate(self):
        metas = {
            "item": frappe.get_meta(self.config.item_doctype),
            "invoice": frappe.get_meta(self.config.invoice_doctype),
            "payment": frappe.get_meta("Payment Entry"),
        }
        rows, payment, header = {}, {}, {}

        for row in self.doc.details_operation_de_caisse or []:
            values = {"item": {}, "payment": {}}
            dims = [("Nature Operations", nature(row).name)]
            for i in range(1, 11):
                source = "imputation_analytique" if i == 1 else f"imputation_analytique_{i}"
                if not row.get(source):
                    continue
                section = frappe.get_doc("Section Analytique", row.get(source))
                axis = frappe.get_doc("Axe Analytique", section.section)
                if not axis.correspondance or not section.compte:
                    frappe.throw(_("Dimension invalide ligne {0}: {1}").format(row.idx, row.get(source)))
                dims.append((axis.correspondance, section.compte))

            for doctype, value in dims:
                found = False
                for target in ("item", "payment", "invoice"):
                    field = self._field(metas[target], doctype)
                    if not field:
                        continue
                    found = True
                    if target == "item":
                        values["item"][field] = value
                    elif target == "payment":
                        if field in payment and payment[field] != value:
                            frappe.throw(_("Plusieurs valeurs pour la dimension {0}").format(doctype))
                        payment[field] = value
                    else:
                        header.setdefault(field, set()).add(value)
                if doctype != "Nature Operations" and not found:
                    frappe.throw(_("Aucun champ ERPNext pour la dimension {0}").format(doctype))

            rows[row.name or str(row.idx)] = frappe._dict(item=values["item"], nature=nature(row))

        return frappe._dict(
            rows=rows,
            payment=payment,
            invoice={k: next(iter(v)) for k, v in header.items() if len(v) == 1},
        )

    @staticmethod
    def _field(meta, dimension_doctype):
        field = frappe.db.get_value(
            "Accounting Dimension", {"document_type": dimension_doctype, "disabled": 0}, "fieldname"
        )
        if field and meta.has_field(field):
            return field
        return next(
            (df.fieldname for df in meta.fields if df.fieldtype == "Link" and df.options == dimension_doctype),
            None,
        )


class InvoiceManager:
    def __init__(self, doc, dimensions):
        self.doc, self.dimensions, self.config = doc, dimensions, cfg(doc)

    def get_or_create(self):
        invoices, missing, parties = {}, [], set()

        for row in self.doc.details_operation_de_caisse or []:
            n = nature(row)
            if row.type_tiers == "Employe" or n.is_advance:
                if row.invoice:
                    frappe.throw(_("Ligne {0}: facture interdite").format(row.idx))
                continue

            row.document_type, row.type_tiers = self.config.invoice_doctype, self.config.detail_tiers_type
            if row.invoice:
                inv = frappe.get_doc(self.config.invoice_doctype, row.invoice)
                if inv.docstatus != 1 or inv.company != company(self.doc):
                    frappe.throw(_("Facture invalide: {0}").format(inv.name))
                if row.tiers and party(row.tiers, self.config) != inv.get(self.config.party_field):
                    frappe.throw(_("Tiers différent de la facture {0}").format(inv.name))
                invoices[inv.name] = inv
                parties.add(inv.get(self.config.party_field))
            else:
                parties.add(party(row.tiers, self.config))
                missing.append(row)

        if len(parties) > 1:
            frappe.throw(_("Une opération ne peut concerner qu'un seul tiers"))

        if missing:
            inv = self._create(missing, next(iter(parties)))
            invoices[inv.name] = inv
            for row in missing:
                row.invoice, row.document_type = inv.name, self.config.invoice_doctype

        return list(invoices.values())

    def _create(self, rows, target_party):
        inv = frappe.new_doc(self.config.invoice_doctype)
        inv.company, inv.posting_date, inv.due_date = company(self.doc), self.doc.date, self.doc.date
        inv.set_posting_time, inv.currency = 1, self.doc.devise
        inv.set(self.config.party_field, target_party)
        if inv.doctype == "Purchase Invoice":
            inv.bill_date = self.doc.date

        pairs = []
        for row in rows:
            n = nature(row)
            item_code = n.item or frappe.db.get_single_value("LS Treso Settings", self.config.default_item)
            if not item_code:
                frappe.throw(_("Item absent pour la nature {0}").format(n.name))
            pairs.append((row, n, inv.append("items", {
                "item_code": item_code, "qty": 1, "rate": flt(row.montant_devise)
            })))

        inv.set_missing_values()
        for row, n, item in pairs:
            item.qty, item.rate = 1, flt(row.montant_devise)
            if item.meta.has_field("custom_nature"):
                item.custom_nature = n.name
            for field, value in self.dimensions.rows[row.name or str(row.idx)].item.items():
                item.set(field, value)
            extra = (n.get("description") or n.get("nature") or n.name or "").strip()
            if extra and extra not in (item.description or ""):
                item.description = f"{item.description or ''}\n{extra}".strip()

        for field, value in self.dimensions.invoice.items():
            inv.set(field, value)

        inv.remarks = _("Créée automatiquement depuis {0} {1}").format(self.doc.doctype, self.doc.name)
        inv.insert()
        inv.submit()
        return inv


class AdvanceManager:
    def __init__(self, doc, invoices):
        self.doc, self.invoices, self.config = doc, invoices, cfg(doc)

    def plan(self, check_available=True):
        if not self.doc.advance_allocation:
            self._totals(0)
            return frappe._dict(distribution=[], reserved={})
        if not self.invoices:
            frappe.throw(_("Une ancienne avance nécessite une facture"))

        first = self.invoices[0]
        target_party, target_company = first.get(self.config.party_field), first.company
        party_currency = frappe.db.get_value(
            "Account", first.get(self.config.party_account_field), "account_currency"
        )
        remaining = self._requested(party_currency)
        reserved, distribution, seen, advance_total = {}, [], set(), 0

        for row in self.doc.advance_allocation:
            if not row.payment_entry or flt(row.allocated_amount) <= 0:
                continue
            if row.payment_entry in seen:
                frappe.throw(_("Payment Entry d'avance dupliqué: {0}").format(row.payment_entry))
            seen.add(row.payment_entry)

            pe = frappe.get_doc("Payment Entry", row.payment_entry)
            valid = (
                pe.docstatus == 1 and pe.payment_type == self.config.payment_type
                and pe.party_type == self.config.party_doctype and pe.party == target_party
                and pe.company == target_company
            )
            if not valid:
                frappe.throw(_("Avance invalide: {0}").format(pe.name))

            row.available_amount = flt(pe.unallocated_amount)
            if check_available and flt(row.allocated_amount, 2) > flt(row.available_amount, 2):
                frappe.throw(_("Montant supérieur au disponible sur {0}").format(pe.name))

            pe_currency = pe.paid_from_account_currency if pe.payment_type == "Receive" else pe.paid_to_account_currency
            if pe_currency != party_currency:
                frappe.throw(_("Devise de compte tiers différente sur {0}").format(pe.name))

            left, allocations = flt(row.allocated_amount), []
            for inv in self.invoices:
                amount = min(left, flt(remaining.get(inv.name)))
                if amount > 0:
                    allocations.append(frappe._dict(invoice=inv.name, amount=amount))
                    reserved[inv.name] = flt(reserved.get(inv.name)) + amount
                    remaining[inv.name] -= amount
                    left -= amount
                if left <= 0:
                    break

            if flt(left, 2) > 0:
                frappe.throw(_("Le montant des avances dépasse le montant des factures"))

            advance_total += convert(row.allocated_amount, pe_currency, self.doc.devise, self.doc.date)
            distribution.append(frappe._dict(payment=pe, allocations=allocations))

        self._totals(advance_total)
        return frappe._dict(distribution=distribution, reserved=reserved)

    def reconcile(self, plan):
        for advance in plan.distribution:
            pe = advance.payment
            recon = frappe.new_doc("Payment Reconciliation")
            recon.company, recon.party_type, recon.party = pe.company, self.config.party_doctype, pe.party
            recon.receivable_payable_account = pe.paid_from if pe.payment_type == "Receive" else pe.paid_to
            recon.payment_name = pe.name
            recon.get_unreconciled_entries()

            payment = next(
                (d for d in recon.payments if d.reference_type == "Payment Entry" and d.reference_name == pe.name),
                None,
            )
            if not payment:
                frappe.throw(_("Aucun montant disponible sur {0}").format(pe.name))

            payment.unreconciled_amount = flt(payment.amount)
            exchange_map = recon.get_invoice_exchange_map(recon.invoices, [payment])
            remaining = flt(payment.amount)

            for alloc in advance.allocations:
                inv = next(
                    (d for d in recon.invoices if d.invoice_type == self.config.invoice_doctype
                     and d.invoice_number == alloc.invoice), None
                )
                if not inv or flt(alloc.amount, 2) > flt(inv.outstanding_amount, 2):
                    frappe.throw(_("Rapprochement impossible: {0} / {1}").format(pe.name, alloc.invoice))

                payment.amount = remaining
                inv.exchange_rate = exchange_map.get(inv.invoice_number)
                row = recon.get_allocated_entry(payment, inv, flt(alloc.amount))
                row.unreconciled_amount = payment.unreconciled_amount
                row.difference_amount = recon.get_difference_amount(payment, inv, flt(alloc.amount))
                row.difference_account = frappe.db.get_value("Company", pe.company, "exchange_gain_loss_account")
                row.exchange_rate, row.gain_loss_posting_date = inv.exchange_rate, self.doc.date
                recon.append("allocation", row)
                remaining -= flt(alloc.amount)

            recon.reconcile()

    def unreconcile(self):
        selections = []
        for advance in self.plan(check_available=False).distribution:
            for alloc in advance.allocations:
                selections.append({
                    "company": advance.payment.company,
                    "voucher_type": "Payment Entry",
                    "voucher_no": advance.payment.name,
                    "against_voucher_type": self.config.invoice_doctype,
                    "against_voucher_no": alloc.invoice,
                })
        if selections:
            create_unreconcile_doc_for_selection(json.dumps(selections))

    def _requested(self, party_currency):
        invoices, result = {i.name: i for i in self.invoices}, {}
        for row in self.doc.details_operation_de_caisse or []:
            if row.invoice in invoices:
                amount = invoice_to_party(invoices[row.invoice], row.montant_devise, party_currency)
                result[row.invoice] = flt(result.get(row.invoice)) + amount
        return result

    def _totals(self, advance_total):
        self.doc.montant_avances_utilisees = flt(advance_total)
        self.doc.montant_total_operation = flt(self.doc.montant) + flt(advance_total)
        detail_total = sum(flt(r.montant_devise) for r in self.doc.details_operation_de_caisse or [])
        if flt(detail_total, 2) != flt(self.doc.montant_total_operation, 2):
            frappe.throw(_("Total détails {0} != total opération {1}").format(
                detail_total, self.doc.montant_total_operation
            ))


class PaymentManager:
    def __init__(self, doc, invoices, dimensions, reserved=None):
        self.doc, self.invoices, self.dimensions = doc, invoices, dimensions
        self.reserved, self.config = reserved or {}, cfg(doc)

    def create(self):
        if flt(self.doc.montant) <= 0:
            return None

        emp = next((r for r in self.doc.details_operation_de_caisse or [] if r.type_tiers == "Employe"), None)
        if emp:
            return self._blank("Employee", employee(emp.tiers))

        if not self.invoices:
            row = next((r for r in self.doc.details_operation_de_caisse or []
                        if r.tiers and nature(r).is_advance), None)
            if not row:
                frappe.throw(_("Aucune facture ou nouvelle avance à traiter"))
            return self._blank(self.config.party_doctype, party(row.tiers, self.config))

        pe = get_payment_entry(
            self.invoices[0].doctype, self.invoices[0].name,
            bank_account=caisse_account(self.doc), payment_type=self.config.payment_type,
            reference_date=self.doc.date,
        )
        self._references(pe)
        self._amount(pe)
        return self._submit(pe)

    def _blank(self, party_type, target_party):
        pe = frappe.new_doc("Payment Entry")
        pe.payment_type, pe.company = self.config.payment_type, company(self.doc)
        pe.party_type, pe.party, pe.posting_date = party_type, target_party, self.doc.date
        pa, cash = get_party_account(party_type, target_party, pe.company), caisse_account(self.doc)
        pe.paid_from, pe.paid_to = (pa, cash) if pe.payment_type == "Receive" else (cash, pa)
        pe.setup_party_account_field()
        pe.set_missing_values()
        pe.set_exchange_rate()
        self._amount(pe)
        return self._submit(pe)

    def _references(self, pe):
        party_currency = pe.paid_from_account_currency if pe.payment_type == "Receive" else pe.paid_to_account_currency
        invoices, requested = {i.name: i for i in self.invoices}, {}

        for row in self.doc.details_operation_de_caisse or []:
            if row.invoice in invoices:
                amount = invoice_to_party(invoices[row.invoice], row.montant_devise, party_currency)
                requested[row.invoice] = flt(requested.get(row.invoice)) + amount

        pe.set("references", [])
        for inv in self.invoices:
            wanted = max(flt(requested.get(inv.name)) - flt(self.reserved.get(inv.name)), 0)
            if wanted <= 0:
                continue
            ref = get_reference_details(inv.doctype, inv.name, party_currency)
            allocated = min(wanted, max(flt(ref.outstanding_amount), 0))
            if allocated > 0:
                pe.append("references", {
                    "reference_doctype": inv.doctype,
                    "reference_name": inv.name,
                    "allocated_amount": allocated,
                })
        pe.set_missing_ref_details(force=True)

    def _amount(self, pe):
        pe.setup_party_account_field()
        pe.set_missing_values()
        pe.set_exchange_rate()

        cash_account = pe.paid_to if pe.payment_type == "Receive" else pe.paid_from
        cash = operation_amount(self.doc, cash_account)
        source, target = flt(pe.source_exchange_rate), flt(pe.target_exchange_rate)

        if pe.payment_type == "Receive":
            pe.received_amount, pe.paid_amount = cash, cash * target / source
        else:
            pe.paid_amount, pe.received_amount = cash, cash * source / target
        pe.set_amounts()

    def _submit(self, pe):
        pe.posting_date, pe.reference_no, pe.reference_date = self.doc.date, self.doc.name, self.doc.date
        pe.remarks = _("{0} LS Tréso {1}").format(self.doc.doctype, self.doc.name)
        for field, value in self.dimensions.payment.items():
            pe.set(field, value)
        pe.submit()
        return pe


def special_operation(doc):
    found = [(row, nature(row)) for row in doc.details_operation_de_caisse or []
             if nature(row).echange or nature(row).solde_initial]
    if not found:
        return None
    if len(found) != 1 or len(doc.details_operation_de_caisse or []) != 1 or doc.advance_allocation:
        frappe.throw(_("Échange / Solde initial: une ligne et aucune avance"))
    row, n = found[0]
    if row.invoice or (n.solde_initial and doc.doctype != "Encaissement"):
        frappe.throw(_("Opération spéciale invalide"))
    return frappe._dict(row=row, nature=n)


def process_special(doc, dimensions, special):
    if special.nature.echange:
        if doc.doctype != "Decaissement":
            return None
        target = frappe.db.get_value("Caisse", doc.remettant, "compte_comptable")
        if not target:
            frappe.throw(_("Caisse destination invalide"))
        return internal_transfer(doc, caisse_account(doc), target, doc.montant,
                                 doc.montant_reference or doc.montant, dimensions.payment)

    if not frappe.db.exists("Account", special.nature.compte_comptable):
        frappe.throw(_("Compte du solde initial invalide"))
    amount = flt(doc.montant_reference or doc.montant)
    return internal_transfer(doc, special.nature.compte_comptable, caisse_account(doc),
                             amount, amount, dimensions.payment)


def internal_transfer(doc, source, target, paid, received, dimensions):
    if source == target or frappe.db.get_value("Account", source, "company") != frappe.db.get_value("Account", target, "company"):
        frappe.throw(_("Comptes de transfert invalides"))

    comp = frappe.db.get_value("Account", source, "company")
    base = frappe.get_cached_value("Company", comp, "default_currency")
    sc, tc = frappe.db.get_value("Account", source, "account_currency"), frappe.db.get_value("Account", target, "account_currency")
    sr = 1 if sc == base else flt(get_exchange_rate(sc, base, doc.date))
    tr = 1 if tc == base else flt(get_exchange_rate(tc, base, doc.date))
    if sc == base:
        tr = flt(paid) / flt(received)
    elif tc == base:
        sr = flt(received) / flt(paid)

    pe = frappe.get_doc({
        "doctype": "Payment Entry", "payment_type": "Internal Transfer", "company": comp,
        "posting_date": doc.date, "reference_no": doc.name, "reference_date": doc.date,
        "paid_from": source, "paid_to": target, "paid_amount": paid, "received_amount": received,
        "source_exchange_rate": sr, "target_exchange_rate": tr,
    })
    for field, value in dimensions.items():
        pe.set(field, value)
    pe.submit()
    return pe


def orchestrator(doc):
    dimensions = DimensionManager(doc).validate()
    special = special_operation(doc)
    if special:
        return process_special(doc, dimensions, special)

    invoices = InvoiceManager(doc, dimensions).get_or_create()
    advances = AdvanceManager(doc, invoices)
    plan = advances.plan()
    payment = PaymentManager(doc, invoices, dimensions, plan.reserved).create()
    advances.reconcile(plan)
    return payment


def cancel(doc):
    config, invoices, seen = cfg(doc), [], set()
    for row in doc.details_operation_de_caisse or []:
        if row.invoice and row.invoice not in seen:
            invoices.append(frappe.get_doc(config.invoice_doctype, row.invoice))
            seen.add(row.invoice)

    AdvanceManager(doc, invoices).unreconcile()
    name = frappe.db.get_value("Payment Entry", {"reference_no": doc.name, "docstatus": 1}, "name")
    if name:
        frappe.get_doc("Payment Entry", name).cancel()
