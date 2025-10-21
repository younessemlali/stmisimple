# -*- coding: utf-8 -*-
from lxml import etree
import re
from io import BytesIO
from typing import Dict, Any, Tuple, Optional, List

HR_NS = "http://ns.hr-xml.org/2004-08-02"
NSMAP = {"hr": HR_NS}

# ---------------- Core XML helpers ----------------
def parse_xml(xml_bytes: bytes) -> etree._ElementTree:
    parser = etree.XMLParser(remove_blank_text=True, recover=True)
    return etree.parse(BytesIO(xml_bytes), parser)

def tostring(tree: etree._ElementTree) -> bytes:
    return etree.tostring(tree, encoding="utf-8", pretty_print=True, xml_declaration=True)

def unified_diff_bytes(before: bytes, after: bytes) -> str:
    import difflib
    a = before.decode("utf-8", errors="ignore").splitlines(keepends=False)
    b = after.decode("utf-8", errors="ignore").splitlines(keepends=False)
    diff = difflib.unified_diff(a, b, fromfile="before.xml", tofile="after.xml", lineterm="")
    return "\n".join(diff)

# -------------- CONTEXT-AWARE (per-contract) helpers --------------
def _rel(xpath: str) -> str:
    """Make an xpath relative to a context element."""
    xp = xpath.strip()
    if xp.startswith("//"):
        return "." + xp
    if not xp.startswith("."):
        return ".//" + xp
    return xp

def find_one_rel(ctx: etree._Element, xpath: str) -> Optional[etree._Element]:
    nodes = ctx.xpath(_rel(xpath), namespaces=NSMAP)
    return nodes[0] if nodes else None

def get_text_rel(ctx: etree._Element, xpath: str) -> str:
    node = find_one_rel(ctx, xpath)
    return (node.text or "").strip() if node is not None and node.text is not None else ""

def ensure_node_rel(ctx: etree._Element, xpath: str) -> etree._Element:
    """
    Creates the node path under the given context element.
    Supports simple paths like hr:Parent/hr:Child/... with an optional leading '//' or './/'.
    """
    # Normalize path and split
    xp = _rel(xpath)
    parts = [p for p in xp.replace(".//", "").split("/") if p]
    # Try to find the final node directly
    node = find_one_rel(ctx, xpath)
    if node is not None:
        return node

    # Walk & create under context
    parent = ctx
    for i, p in enumerate(parts):
        # find existing child matching the path prefix
        subxp = ".//" + "/".join(parts[:i+1])
        found = ctx.xpath(subxp, namespaces=NSMAP)
        if found:
            parent = found[0]
            continue
        # create the missing element
        tag = p.replace("hr:", "{%s}" % HR_NS)
        new_el = etree.Element(tag)
        parent.append(new_el)
        parent = new_el
    return parent

def set_text_rel(ctx: etree._Element, xpath: str, value: str) -> None:
    node = find_one_rel(ctx, xpath)
    if node is None:
        node = ensure_node_rel(ctx, xpath)
    node.text = value

# -------------- Discovery of "contract" contexts --------------
def find_contract_contexts(tree: etree._ElementTree) -> List[etree._Element]:
    """
    Heuristic: a 'contract' context is any element that contains a <ReferenceInformation>
    with BOTH <OrderId><IdValue> and <AssignmentId><IdValue> below it.
    """
    parents = tree.xpath(
        "//hr:ReferenceInformation[hr:OrderId/hr:IdValue and hr:AssignmentId/hr:IdValue]/..",
        namespaces=NSMAP
    )
    # Deduplicate while preserving order
    seen = set()
    result = []
    for p in parents:
        if id(p) not in seen:
            seen.add(id(p))
            result.append(p)
    return result

# -------------- Single-context operations --------------
def extract_order_id_ctx(ctx: etree._Element) -> str:
    return get_text_rel(ctx, "//hr:ReferenceInformation/hr:OrderId/hr:IdValue")

def extract_assignment_id_ctx(ctx: etree._Element) -> str:
    return get_text_rel(ctx, "//hr:ReferenceInformation/hr:AssignmentId/hr:IdValue")

def normalize_classification_ctx(ctx: etree._Element, class_regex: str = r"^[A-E]\d{1,2}$") -> Tuple[bool, str]:
    coeff_xp = "//hr:PositionCharacteristics/hr:PositionCoefficient"
    level_xp = "//hr:PositionCharacteristics/hr:PositionLevel"
    coeff = get_text_rel(ctx, coeff_xp)
    level = get_text_rel(ctx, level_xp)
    if not coeff and re.match(class_regex, level or ""):
        set_text_rel(ctx, coeff_xp, level)
        return True, level
    return False, coeff or ""

def apply_command_mappings_ctx(ctx: etree._Element, cmd: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    applied = {}
    mappings: Dict[str, str] = cfg.get("mappings", {})
    statut_map: Dict[str, str] = cfg.get("statut_map", {})

    if "classification_interimaire" in cmd and "classification_interimaire" in mappings:
        value = str(cmd["classification_interimaire"]).strip()
        if value:
            set_text_rel(ctx, mappings["classification_interimaire"], value)
            applied["classification_interimaire"] = value

    if "statut" in cmd and "statut" in mappings:
        raw = str(cmd["statut"]).strip()
        value = statut_map.get(raw, raw) if raw else ""
        if value:
            set_text_rel(ctx, mappings["statut"], value)
            applied["statut"] = value

    if "personne_absente" in cmd and "personne_absente" in mappings:
        value = str(cmd["personne_absente"]).strip()
        if value:
            set_text_rel(ctx, mappings["personne_absente"], value)
            set_text_rel(ctx, "//hr:ContractInformation/hr:ContractLegalReason/hr:RecourseType", "01")
            applied["personne_absente"] = value
            applied["recourse_type"] = "01"

    if "code_metier" in cmd and "code_metier" in mappings:
        value = str(cmd["code_metier"]).strip()
        if value:
            set_text_rel(ctx, mappings["code_metier"], value)
            applied["code_metier"] = value

    if "code_site" in cmd and "code_site" in mappings:
        value = str(cmd["code_site"]).strip()
        if value:
            curr = get_text_rel(ctx, mappings["code_site"])
            site_cfg = cfg.get("site_idvalue", {})
            rebuild = site_cfg.get("rebuild", False)
            if rebuild and site_cfg.get("siret_prefix"):
                set_text_rel(ctx, mappings["code_site"], f"{site_cfg['siret_prefix']}-{value}")
            else:
                if curr and "-" in curr:
                    prefix = curr.split("-")[0]
                    set_text_rel(ctx, mappings["code_site"], f"{prefix}-{value}")
                else:
                    set_text_rel(ctx, mappings["code_site"], value)
            applied["code_site"] = value

    return applied

def summarize_ctx(ctx: etree._Element) -> Dict[str, Any]:
    return {
        "OrderId": extract_order_id_ctx(ctx),
        "AssignmentId": extract_assignment_id_ctx(ctx),
        "EU": get_text_rel(ctx, "//hr:ReferenceInformation/hr:StaffingCustomerId/hr:IdValue"),
        "OrgUnit": get_text_rel(ctx, "//hr:ReferenceInformation/hr:StaffingCustomerOrgUnitId/hr:IdValue"),
        "Agency": get_text_rel(ctx, "//hr:ReferenceInformation/hr:AgencyId/hr:IdValue"),
        "StatusCode": get_text_rel(ctx, "//hr:PositionCharacteristics/hr:PositionStatus/hr:Code"),
        "PositionLevel": get_text_rel(ctx, "//hr:PositionCharacteristics/hr:PositionLevel"),
        "PositionCoefficient": get_text_rel(ctx, "//hr:PositionCharacteristics/hr:PositionCoefficient"),
        "PersonReplaced": get_text_rel(ctx, "//hr:ContractInformation/hr:ContractLegalReason/hr:PersonReplaced"),
    }

# -------------- Public APIs --------------
def extract_order_id(tree: etree._ElementTree) -> str:
    """
    Kept for backward compatibility (first contract in doc).
    """
    contexts = find_contract_contexts(tree)
    return extract_order_id_ctx(contexts[0]) if contexts else ""

def process_one(xml_bytes: bytes, cmd_row: Optional[Dict[str, Any]], cfg: Dict[str, Any]):
    """
    Backward-compatible single-context processing (first contract only).
    """
    tree = parse_xml(xml_bytes)
    before = tostring(tree)
    contexts = find_contract_contexts(tree)
    if not contexts:
        after = tostring(tree)
        return after, {}, unified_diff_bytes(before, after)
    # Apply to first context only
    ctx = contexts[0]
    rules = cfg.get("rules", {})
    if rules.get("normalize_coefficient_from_level", True):
        normalize_classification_ctx(ctx, rules.get("classification_regex", r"^[A-E]\d{1,2}$"))
    if cmd_row:
        apply_command_mappings_ctx(ctx, cmd_row, cfg)
    after = tostring(tree)
    summary = summarize_ctx(ctx)
    return after, summary, unified_diff_bytes(before, after)

def process_all(xml_bytes: bytes, cmd_records: Dict[str, Dict[str, Any]], cfg: Dict[str, Any]):
    """
    Process ALL contracts within a single XML document.
    Returns (xml_fixed, summaries_list, diff_whole_doc).
    """
    tree = parse_xml(xml_bytes)
    before = tostring(tree)
    contexts = find_contract_contexts(tree)
    summaries: List[Dict[str, Any]] = []

    rules = cfg.get("rules", {})
    for ctx in contexts:
        # Normalize
        if rules.get("normalize_coefficient_from_level", True):
            normalize_classification_ctx(ctx, rules.get("classification_regex", r"^[A-E]\d{1,2}$"))
        # Match command row by OrderId
        key = extract_order_id_ctx(ctx)
        cmd_row = cmd_records.get(key) if key else None
        if cmd_row:
            apply_command_mappings_ctx(ctx, cmd_row, cfg)
        s = summarize_ctx(ctx)
        s["matched"] = bool(cmd_row)
        summaries.append(s)

    after = tostring(tree)
    diff = unified_diff_bytes(before, after)
    return after, summaries, diff


def split_fixed_by_contract(fixed_xml_bytes: bytes):
    """
    Reparse the already fixed XML and split into N XML files,
    each containing exactly one contract context.
    Returns: List[ (order_id, assignment_id, bytes_xml) ]
    """
    tree = parse_xml(fixed_xml_bytes)
    contexts = find_contract_contexts(tree)
    results = []
    for i, ctx in enumerate(contexts):
        # Deep copy the whole doc
        root_copy = etree.fromstring(etree.tostring(tree.getroot()))
        tree_copy = etree.ElementTree(root_copy)
        # Find contexts in the copy (same order)
        ctxs_copy = find_contract_contexts(tree_copy)
        for j, ctx_copy in enumerate(ctxs_copy):
            if j != i:
                parent = ctx_copy.getparent()
                if parent is not None:
                    parent.remove(ctx_copy)
        # Extract keys from the kept context
        kept_ctx = find_contract_contexts(tree_copy)[0]
        order_id = extract_order_id_ctx(kept_ctx) or f"NOORDER_{i+1:03d}"
        assign_id = extract_assignment_id_ctx(kept_ctx) or f"NOASSIGN_{i+1:03d}"
        results.append((order_id, assign_id, tostring(tree_copy)))
    return results
