# -*- coding: utf-8 -*-
import streamlit as st
import pandas as pd
import yaml, json, io, zipfile, re
from typing import Dict, Any
from processor import process_all, extract_order_id, parse_xml, split_fixed_by_contract

st.set_page_config(page_title="PIXID XML Fixer — Multi-Contrats", layout="wide")
st.title("🔧 PIXID XML Fixer — Multi-Contrats dans un même XML")

st.markdown("""
Déposez un **XML contenant N contrats** (ex: 60). L'app détecte **tous les `OrderId`**, 
applique les règles/mappings pour **chaque contrat**, et génère un **XML corrigé unique** (+ CSV + diff).
""")

# Uploads
xml_file = st.file_uploader("📄 XML avec plusieurs contrats", type=["xml"])
cmd_file = st.file_uploader("🧾 Commandes (JSON ou CSV)", type=["json", "csv"])
cfg_file = st.file_uploader("⚙️ Configuration (config.yaml)", type=["yaml", "yml"])

# Config
if cfg_file:
    cfg = yaml.safe_load(cfg_file.read())
else:
    cfg = yaml.safe_load(open("config.yaml","r",encoding="utf-8").read())

st.subheader("🛠️ Configuration")
cfg_text = st.text_area("config.yaml (éditable)", value=yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), height=280)
cfg = yaml.safe_load(cfg_text) if cfg_text else cfg

# Commandes dict
cmd_records: Dict[str, Dict[str, Any]] = {}
if cmd_file:
    name = cmd_file.name.lower()
    if name.endswith(".json"):
        data = json.load(cmd_file)
        if isinstance(data, list):
            for row in data:
                key = str(row.get("numero_commande","")).strip()
                if key:
                    cmd_records[key] = {k:(v.strip() if isinstance(v,str) else v) for k,v in row.items()}
        elif isinstance(data, dict):
            for key, row in data.items():
                skey = str(key).strip()
                if skey:
                    rec = row if isinstance(row, dict) else {"value": row}
                    rec = {k:(v.strip() if isinstance(v,str) else v) for k,v in rec.items()}
                    cmd_records[skey] = rec
        else:
            st.warning("JSON inattendu: fournissez une liste d'objets ou un dict {numero_commande: {...}}.")
    else:
        df = pd.read_csv(cmd_file, sep=None, engine="python", dtype=str)
        if "numero_commande" not in df.columns:
            st.warning("Colonne 'numero_commande' absente du CSV.")
        else:
            for _, row in df.iterrows():
                key = (row.get("numero_commande") or "").strip()
                if key:
                    cmd_records[key] = {k:(v.strip() if isinstance(v,str) else v) for k,v in row.to_dict().items()}

if cmd_records:
    st.success(f"{len(cmd_records)} commandes chargées. Ex: {list(cmd_records.keys())[:5]}")

# Action
split_opt = st.checkbox("✨ Scinder en 1 fichier par contrat (ZIP)", value=False)
progress = st.progress(0)
go = st.button("🚀 Traiter le XML", type="primary", disabled=not xml_file)

if go and xml_file:
    raw = xml_file.read()
    try:
        fixed_xml, summaries, diff = process_all(raw, cmd_records, cfg)
    except Exception as e:
        st.error(f"Erreur: {e}")
    else:
        st.success(f"{len(summaries)} contrats détectés et traités.")
        # Afficher un tableau
        df = pd.DataFrame(summaries)
        st.dataframe(df, use_container_width=True)

        # Téléchargements
        st.download_button("⬇️ Télécharger le XML corrigé", data=fixed_xml, file_name=xml_file.name.replace(".xml","_fixed.xml"), mime="application/xml")
        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Export CSV récapitulatif", data=csv, file_name="recap.csv", mime="text/csv")


        # Rapport correspondances
        matched = sum(1 for s in summaries if s.get("matched"))
        unmatched = len(summaries) - matched
        st.write(f"**Correspondances commandes** : {matched} trouvées / {len(summaries)} contrats, {unmatched} sans correspondance.")

        # Option de split
        if split_opt:
            st.info("Scission en cours…")
            parts = split_fixed_by_contract(fixed_xml)
            zbuf2 = io.BytesIO()
            with zipfile.ZipFile(zbuf2, "w", compression=zipfile.ZIP_DEFLATED) as zout2:
                for k, (order_id, assign_id, xml_part) in enumerate(parts, 1):
                    fname = f\"{order_id or 'NOORDER'}__{assign_id or 'NOASSIGN'}.xml\"
                    zout2.writestr(fname, xml_part)
                    progress.progress(min(100, int(k*100/max(1,len(parts)))))
            zbuf2.seek(0)
            st.download_button(\"⬇️ Télécharger le ZIP (1 XML / contrat)\", data=zbuf2, file_name=\"split_contracts.zip\", mime=\"application/zip\")

        with st.expander("🧾 Diff complet (avant ↔ après)", expanded=False):
            st.code(diff or "Aucun changement", language="diff")

st.caption("Prend en charge **tous** les contrats contenus dans un même fichier XML (multi-contrats).")
