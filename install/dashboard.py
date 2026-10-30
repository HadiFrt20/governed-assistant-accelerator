"""AI/BI dashboard (usage & governance) for the assistant. Written against placeholders that build() replaces:
__FQ__ schema, __NAME__ assistant name, __COMPANY__, __SITE__, brand colours __PRIMARY__ __PRIMARY2__ __ACCENT__ __SECONDARY__."""
import json

DS_TECH = [
    {"name": "ds_tech", "displayName": "Appels AI Gateway (technique)", "queryLines": [
        "SELECT event_time, jour, COALESCE(pilier, 'Autre') AS pilier, service_metier, ",
        "CASE WHEN bloque_par_politique THEN 'Bloqué par politique' WHEN modele IS NOT NULL THEN modele ELSE 'Genie One MCP' END AS modele, ",
        "CASE WHEN service_type = 'MCP_SERVICE' THEN COALESCE(outil_mcp, methode_mcp) END AS composant_mcp, ",
        "CASE WHEN outil_mcp IS NOT NULL THEN 1 ELSE 0 END AS appel_outil_mcp, ",
        "COALESCE(input_tokens, 0) AS tokens_entree, COALESCE(output_tokens, 0) AS tokens_sortie, latency_ms, ttfb_ms, ",
        "CAST(status_code AS STRING) AS code_http, CASE WHEN bloque_par_politique THEN 1 ELSE 0 END AS bloque ",
        "FROM __FQ__.gold_gateway_usage"]},
    {"name": "ds_latency", "displayName": "Latences par composant", "queryLines": [
        "SELECT jour, CASE WHEN service_type = 'MODEL_SERVICE' THEN COALESCE(modele, 'Bloqué par politique') ",
        "ELSE CONCAT('Genie One · ', COALESCE(outil_mcp, methode_mcp)) END AS composant, ",
        "percentile_approx(latency_ms, 0.5) AS p50_ms, percentile_approx(latency_ms, 0.95) AS p95_ms, COUNT(*) AS appels ",
        "FROM __FQ__.gold_gateway_usage GROUP BY ALL ORDER BY p95_ms DESC"]},
    {"name": "ds_lat_llm", "displayName": "Latence LLM", "queryLines": [
        "SELECT DATE_TRUNC('HOUR', event_time) AS event_time, jour, percentile_approx(latency_ms, 0.5) AS p50_ms, percentile_approx(latency_ms, 0.95) AS p95_ms, ",
        "AVG(ttfb_ms) AS ttfb_ms FROM gold_gateway_usage WHERE service_type = 'MODEL_SERVICE' AND modele IS NOT NULL GROUP BY ALL"]},
    {"name": "ds_tools", "displayName": "Outils de l'agent", "queryLines": [
        "SELECT turn_id, user_email, service_declare, created_at, jour, outil, action FROM gold_agent_tools"]},
    {"name": "ds_guard_cfg", "displayName": "Configuration des garde-fous", "queryLines": [
        "SELECT service, modele, politique, description, type_politique, implementation, phases, action, rang, limite_debit, table_inference ",
        "FROM __FQ__.ref_guardrail_config ORDER BY service, rang"]},
]

DS = [
    {"name": "ds_requests", "displayName": "Demandes __NAME__", "queryLines": [
        "SELECT turn_id, user_email, service_declare, metier_infere, type_usage, livrable, ",
        "CASE WHEN bloque THEN 1 ELSE 0 END AS bloquee, CASE WHEN livrable IN ('PowerPoint', 'Word') THEN 1 ELSE 0 END AS document_office, ",
        "COALESCE(politique_bloquante, '') AS politique_bloquante, appels_genie_one, created_at, jour, user_text ",
        "FROM __FQ__.gold_assistant_requests ORDER BY created_at DESC"]},
    {"name": "ds_gateway", "displayName": "Trafic AI Gateway", "queryLines": [
        "SELECT event_time, jour, COALESCE(pilier, 'Autre') AS pilier, utilisateur, service_metier, COALESCE(agent_code, '') AS agent_code, ",
        "COALESCE(outil_mcp, '') AS outil_mcp, COALESCE(modele, 'Genie One') AS modele, COALESCE(total_tokens, 0) AS total_tokens, ",
        "COALESCE(input_tokens, 0) AS input_tokens, COALESCE(output_tokens, 0) AS output_tokens, latency_ms, status_code, ",
        "COALESCE(cout_estime_usd, 0) AS cout_estime_usd, CASE WHEN status_code >= 400 THEN 1 ELSE 0 END AS erreur ",
        "FROM __FQ__.gold_gateway_usage"]},
    {"name": "ds_policies", "displayName": "Décisions des politiques", "queryLines": [
        "SELECT event_time, jour, pilier, utilisateur, service_metier, politique, phase, decision, ",
        "CASE WHEN decision = 'DENY' THEN 1 ELSE 0 END AS refus, COALESCE(raison, '') AS raison ",
        "FROM __FQ__.gold_policy_decisions"]},
]


def text(name, md, x, y, w, h=1):
    return {"widget": {"name": name, "multilineTextboxSpec": {"lines": [md]}}, "position": {"x": x, "y": y, "width": w, "height": h}}


def counter(name, title, ds, expr, fname, x, y, fmt=None, desc=None, period="event_time"):
    enc = {"value": {"fieldName": fname, "displayName": title, **({"format": fmt} if fmt else {})},
           "period": {"fieldName": f"hourly({period})"}}
    frame = {"showTitle": True, "title": title, **({"description": desc, "showDescription": True} if desc else {})}
    return {"widget": {"name": name, "queries": [{"name": "main_query", "query": {"datasetName": ds, "fields": [
        {"name": fname, "expression": expr}, {"name": f"hourly({period})", "expression": f'DATE_TRUNC("HOUR", `{period}`)'}],
        "disaggregated": False}}],
        "spec": {"version": 2, "widgetType": "counter", "encodings": enc, "frame": frame}},
        "position": {"x": x, "y": y, "width": 3, "height": 3}}


def bar(name, title, ds, cat, val_expr, val_name, val_label, x, y, w=6, h=6, color=None, horizontal=True, mappings=None, desc=None):
    fields = [{"name": cat, "expression": f"`{cat}`"}, {"name": val_name, "expression": val_expr}]
    if color:
        fields.append({"name": color, "expression": f"`{color}`"})
    q = {"fieldName": val_name, "displayName": val_label, "scale": {"type": "quantitative"}}
    c = {"fieldName": cat, "displayName": cat.replace("_", " "), "scale": {"type": "categorical", "sort": {"by": "y-reversed" if horizontal else "y-reversed"}}}
    c["scale"] = {"type": "categorical"}
    enc = {"x": q, "y": c} if horizontal else {"x": c, "y": q}
    if color:
        enc["color"] = {"fieldName": color, "displayName": color.replace("_", " "), "scale": {"type": "categorical", **({"mappings": mappings} if mappings else {})}}
    frame = {"showTitle": True, "title": title, **({"description": desc, "showDescription": True} if desc else {})}
    return {"widget": {"name": name, "queries": [{"name": "main_query", "query": {"datasetName": ds, "fields": fields, "disaggregated": False}}],
                       "spec": {"version": 3, "widgetType": "bar", "encodings": enc, "frame": frame}},
            "position": {"x": x, "y": y, "width": w, "height": h}}


def timebar(name, title, ds, color, val_expr, val_name, val_label, x, y, w=12, h=6):
    return {"widget": {"name": name, "queries": [{"name": "main_query", "query": {"datasetName": ds, "fields": [
        {"name": "hourly(event_time)", "expression": 'DATE_TRUNC("HOUR", `event_time`)'},
        {"name": val_name, "expression": val_expr}, {"name": color, "expression": f"`{color}`"}], "disaggregated": False}}],
        "spec": {"version": 3, "widgetType": "bar", "encodings": {
            "x": {"fieldName": "hourly(event_time)", "displayName": "Heure", "scale": {"type": "temporal"}},
            "y": {"fieldName": val_name, "displayName": val_label, "scale": {"type": "quantitative"}},
            "color": {"fieldName": color, "displayName": "Pilier", "scale": {"type": "categorical", "mappings": [
                {"value": "Bureautique", "color": "__PRIMARY2__"}, {"value": "Développeur", "color": "__SECONDARY__"},
                {"value": "Genie One (données & documents)", "color": "__ACCENT__"}]}}},
            "frame": {"showTitle": True, "title": title}}},
        "position": {"x": x, "y": y, "width": w, "height": h}}


def table(name, title, ds, cols, x, y, w=12, h=7, desc=None):
    frame = {"showTitle": True, "title": title, **({"description": desc, "showDescription": True} if desc else {})}
    return {"widget": {"name": name, "queries": [{"name": "main_query", "query": {"datasetName": ds,
        "fields": [{"name": c, "expression": f"`{c}`"} for c, _ in cols], "disaggregated": True}}],
        "spec": {"version": 2, "widgetType": "table", "encodings": {"columns": [{"fieldName": c, "displayName": d} for c, d in cols]}, "frame": frame}},
        "position": {"x": x, "y": y, "width": w, "height": h}}


def agg_table(name, title, ds, dims, measures, x, y, w=12, h=6):
    fields = [{"name": d, "expression": f"`{d}`"} for d, _ in dims] + [{"name": n, "expression": e} for n, e, _, _ in measures]
    cols = [{"fieldName": d, "displayName": l} for d, l in dims] + [{"fieldName": n, "displayName": l, **({"format": f} if f else {})} for n, _, l, f in measures]
    return {"widget": {"name": name, "queries": [{"name": "main_query", "query": {"datasetName": ds, "fields": fields, "disaggregated": False}}],
                       "spec": {"version": 2, "widgetType": "table", "encodings": {"columns": cols}, "frame": {"showTitle": True, "title": title}}},
            "position": {"x": x, "y": y, "width": w, "height": h}}


USD = {"type": "number-currency", "currencyCode": "USD", "decimalPlaces": {"type": "max", "places": 2}}
COMPACT = {"type": "number", "abbreviation": "compact"}

p1 = [
    text("t1", "## __NAME__ — adoption et usages", 0, 0, 12),
    text("t1s", "Demandes des équipes de __SITE__, classées automatiquement par `ai_classify` (type d'usage, métier). Source : historique de l'assistant (Lakebase) exposé dans Unity Catalog.", 0, 1, 12),
    counter("k_req", "Demandes", "ds_requests", "COUNT(`turn_id`)", "count(turn_id)", 0, 2, period="created_at"),
    counter("k_docs", "Documents Office générés", "ds_requests", "SUM(`document_office`)", "sum(document_office)", 3, 2, period="created_at", desc="PowerPoint et Word au format __COMPANY__"),
    counter("k_users", "Utilisateurs actifs", "ds_requests", "COUNT(DISTINCT `user_email`)", "countdistinct(user_email)", 6, 2, period="created_at"),
    counter("k_block", "Demandes bloquées", "ds_requests", "SUM(`bloquee`)", "sum(bloquee)", 9, 2, period="created_at", desc="Politiques anti-fuite PI et anti-jailbreak"),
    bar("b_type", "Types d'usage (ai_classify)", "ds_requests", "type_usage", "COUNT(`turn_id`)", "count(turn_id)", "Demandes", 0, 5, color="livrable",
        mappings=[{"value": "PowerPoint", "color": "#C4471F"}, {"value": "Word", "color": "#2B5DB8"}, {"value": "Réponse dans le chat", "color": "#9BD3A0"}, {"value": "Bloqué", "color": "#D9534F"}]),
    bar("b_dept", "Demandes par service et métier inféré", "ds_requests", "service_declare", "COUNT(`turn_id`)", "count(turn_id)", "Demandes", 6, 5, color="metier_infere",
        desc="Service déclaré dans l'assistant ; couleur = métier inféré par ai_classify"),
    table("tb_req", "Dernières demandes", "ds_requests", [("created_at", "Date"), ("user_email", "Utilisateur"), ("service_declare", "Service"),
          ("type_usage", "Type d'usage (IA)"), ("metier_infere", "Métier (IA)"), ("livrable", "Livrable"), ("politique_bloquante", "Politique"), ("user_text", "Demande")], 0, 11),
]
p2 = [
    text("t2", "## IA gouvernée — Unity AI Gateway", 0, 0, 12),
    text("t2s", "Tout le trafic IA de __COMPANY__ passe par Unity AI Gateway : identité de l'utilisateur (OBO), politiques, traçabilité, coûts. Coût estimé au prix catalogue ; Genie One gratuit pour les utilisateurs jusqu'au 31/01/2027.", 0, 1, 12),
    counter("k_calls", "Appels AI Gateway", "ds_gateway", "COUNT(`event_time`)", "count(event_time)", 0, 2),
    counter("k_tok", "Tokens consommés", "ds_gateway", "SUM(`total_tokens`)", "sum(total_tokens)", 3, 2, fmt=COMPACT),
    counter("k_cost", "Coût estimé", "ds_gateway", "SUM(`cout_estime_usd`)", "sum(cout_estime_usd)", 6, 2, fmt=USD, desc="Prix catalogue configuré"),
    counter("k_err", "Appels en erreur", "ds_gateway", "SUM(`erreur`)", "sum(erreur)", 9, 2),
    timebar("tb_hour", "Appels par heure et par pilier", "ds_gateway", "pilier", "COUNT(`event_time`)", "count(event_time)", "Appels", 0, 5),
    bar("b_tokdept", "Tokens par service métier", "ds_gateway", "service_metier", "SUM(`total_tokens`)", "sum(total_tokens)", "Tokens", 0, 11, color="pilier",
        mappings=[{"value": "Bureautique", "color": "__PRIMARY2__"}, {"value": "Développeur", "color": "__SECONDARY__"}, {"value": "Genie One (données & documents)", "color": "__ACCENT__"}]),
    bar("b_pol", "Décisions des politiques de sécurité", "ds_policies", "politique", "COUNT(`event_time`)", "count(event_time)", "Évaluations", 6, 11, color="decision",
        mappings=[{"value": "ALLOW", "color": "__ACCENT__"}, {"value": "DENY", "color": "#D9534F"}, {"value": "TRANSFORM", "color": "#F2A93B"}]),
    agg_table("tb_user", "Consommation par utilisateur et pilier", "ds_gateway", [("utilisateur", "Utilisateur"), ("pilier", "Pilier")],
              [("count(event_time)", "COUNT(`event_time`)", "Appels", None), ("sum(total_tokens)", "SUM(`total_tokens`)", "Tokens", COMPACT),
               ("avg(latency_ms)", "AVG(`latency_ms`)", "Latence moy. (ms)", {"type": "number", "decimalPlaces": {"type": "max", "places": 0}}),
               ("sum(cout_estime_usd)", "SUM(`cout_estime_usd`)", "Coût estimé", USD)], 0, 17),
]
MS = {"type": "number-plain", "decimalPlaces": {"type": "max", "places": 0}, "suffix": " ms"}


def multibar(name, title, ds, cat, ys, x, y, w=6, h=6, horizontal=False, desc=None):
    fields = [{"name": cat, "expression": f"`{cat}`"}] + [{"name": n, "expression": e} for n, e, _ in ys]
    q = {"scale": {"type": "quantitative"}, "fields": [{"fieldName": n, "displayName": l} for n, _, l in ys]}
    c = {"fieldName": cat, "displayName": cat.replace("_", " "), "scale": {"type": "categorical"}}
    enc = {"x": q, "y": c} if horizontal else {"x": c, "y": q}
    frame = {"showTitle": True, "title": title, **({"description": desc, "showDescription": True} if desc else {})}
    return {"widget": {"name": name, "queries": [{"name": "main_query", "query": {"datasetName": ds, "fields": fields, "disaggregated": False}}],
                       "spec": {"version": 3, "widgetType": "bar", "encodings": enc, "mark": {"layout": "group"}, "frame": frame}},
            "position": {"x": x, "y": y, "width": w, "height": h}}


p3 = [
    text("t3", "## Technique — latence, modèles, garde-fous, MCP et outils", 0, 0, 12),
    text("t3s", "Vue d'architecte : performance des appels Unity AI Gateway, consommation de tokens par modèle, décisions des garde-fous, appels MCP à Genie One et outils de l'agent.", 0, 1, 12),
    counter("k_p50", "Latence LLM p50", "ds_lat_llm", "AVG(`p50_ms`)", "avg(p50_ms)", 0, 2, fmt=MS),
    counter("k_p95", "Latence LLM p95", "ds_lat_llm", "MAX(`p95_ms`)", "max(p95_ms)", 3, 2, fmt=MS),
    counter("k_ttfb", "Time to first byte (LLM)", "ds_lat_llm", "AVG(`ttfb_ms`)", "avg(ttfb_ms)", 6, 2, fmt=MS),
    counter("k_mcp", "Appels d'outils MCP", "ds_tech", "SUM(`appel_outil_mcp`)", "sum(appel_outil_mcp)", 9, 2, desc="genie_ask, genie_poll_response…"),
    multibar("b_lat", "Latence p50 / p95 par composant", "ds_latency", "composant", [("max(p50_ms)", "MAX(`p50_ms`)", "p50 (ms)"), ("max(p95_ms)", "MAX(`p95_ms`)", "p95 (ms)")], 0, 5, horizontal=True),
    multibar("b_tok", "Tokens par modèle", "ds_tech", "modele", [("sum(tokens_entree)", "SUM(`tokens_entree`)", "Entrée"), ("sum(tokens_sortie)", "SUM(`tokens_sortie`)", "Sortie")], 6, 5),
    bar("b_mcp", "Appels MCP Genie One par outil / méthode JSON-RPC", "ds_tech", "composant_mcp", "COUNT(`event_time`)", "count(event_time)", "Appels", 0, 11, color="pilier"),
    bar("b_tools", "Outils appelés par l'agent", "ds_tools", "outil", "COUNT(`turn_id`)", "count(turn_id)", "Appels", 6, 11, color="action"),
    bar("b_guard", "Garde-fous : décisions par politique", "ds_policies", "politique", "COUNT(`event_time`)", "count(event_time)", "Évaluations", 0, 17, color="decision",
        mappings=[{"value": "ALLOW", "color": "__ACCENT__"}, {"value": "DENY", "color": "#D9534F"}, {"value": "TRANSFORM", "color": "#F2A93B"}]),
    bar("b_http", "Codes HTTP des appels", "ds_tech", "code_http", "COUNT(`event_time`)", "count(event_time)", "Appels", 6, 17, color="pilier", horizontal=False),
    table("tb_guard_cfg", "Configuration des garde-fous (Unity AI Gateway)", "ds_guard_cfg", [("service", "Service"), ("modele", "Modèle"), ("politique", "Politique"),
          ("description", "Rôle"), ("type_politique", "Type"), ("phases", "Phases"), ("action", "Action"), ("limite_debit", "Limite de débit"), ("table_inference", "Table d'inférence")], 0, 23, h=6),
    table("tb_guard_log", "Dernières décisions des garde-fous", "ds_policies", [("event_time", "Date"), ("utilisateur", "Utilisateur"), ("pilier", "Pilier"),
          ("politique", "Politique"), ("phase", "Phase"), ("decision", "Décision"), ("raison", "Raison")], 0, 29, h=6),
    agg_table("tb_models", "Détail par modèle et pilier", "ds_tech", [("modele", "Modèle"), ("pilier", "Pilier")],
              [("count(event_time)", "COUNT(`event_time`)", "Appels", None), ("sum(tokens_entree)", "SUM(`tokens_entree`)", "Tokens entrée", COMPACT),
               ("sum(tokens_sortie)", "SUM(`tokens_sortie`)", "Tokens sortie", COMPACT), ("avg(latency_ms)", "AVG(`latency_ms`)", "Latence moy.", MS),
               ("avg(ttfb_ms)", "AVG(`ttfb_ms`)", "TTFB moy.", MS)], 0, 35),
]

filters = [
    {"widget": {"name": "f_jour", "queries": [
        {"name": "q_req_jour", "query": {"datasetName": "ds_requests", "fields": [{"name": "jour", "expression": "`jour`"}], "disaggregated": False}},
        {"name": "q_gw_jour", "query": {"datasetName": "ds_gateway", "fields": [{"name": "jour", "expression": "`jour`"}], "disaggregated": False}},
        {"name": "q_pol_jour", "query": {"datasetName": "ds_policies", "fields": [{"name": "jour", "expression": "`jour`"}], "disaggregated": False}}] +
        [{"name": f"q_{d}_jour", "query": {"datasetName": d, "fields": [{"name": "jour", "expression": "`jour`"}], "disaggregated": False}} for d in ("ds_tech", "ds_latency", "ds_lat_llm", "ds_tools")],
        "spec": {"version": 2, "widgetType": "filter-date-range-picker", "encodings": {"fields": [
            {"fieldName": "jour", "displayName": "Jour", "queryName": q} for q in ("q_req_jour", "q_gw_jour", "q_pol_jour", "q_ds_tech_jour", "q_ds_latency_jour", "q_ds_lat_llm_jour", "q_ds_tools_jour")]},
            "frame": {"showTitle": True, "title": "Période"}}}, "position": {"x": 0, "y": 0, "width": 4, "height": 2}},
    {"widget": {"name": "f_service", "queries": [
        {"name": "q_gw_srv", "query": {"datasetName": "ds_gateway", "fields": [{"name": "service_metier", "expression": "`service_metier`"}], "disaggregated": False}},
        {"name": "q_pol_srv", "query": {"datasetName": "ds_policies", "fields": [{"name": "service_metier", "expression": "`service_metier`"}], "disaggregated": False}}],
        "spec": {"version": 2, "widgetType": "filter-multi-select", "encodings": {"fields": [
            {"fieldName": "service_metier", "displayName": "Service", "queryName": q} for q in ("q_gw_srv", "q_pol_srv")]},
            "frame": {"showTitle": True, "title": "Service métier (AI Gateway)"}}}, "position": {"x": 4, "y": 0, "width": 4, "height": 2}},
]
dash = {
    "datasets": DS + DS_TECH,
    "pages": [
        {"name": "adoption", "displayName": "Adoption & usages", "pageType": "PAGE_TYPE_CANVAS", "layoutVersion": "GRID_V1", "layout": p1},
        {"name": "gouvernance", "displayName": "IA gouvernée (AI Gateway)", "pageType": "PAGE_TYPE_CANVAS", "layoutVersion": "GRID_V1", "layout": p2},
        {"name": "technique", "displayName": "Technique", "pageType": "PAGE_TYPE_CANVAS", "layoutVersion": "GRID_V1", "layout": p3},
        {"name": "filtres", "displayName": "Filtres", "pageType": "PAGE_TYPE_GLOBAL_FILTERS", "layoutVersion": "GRID_V1", "layout": filters},
    ],
    "uiSettings": {"theme": {
        "canvasBackgroundColor": {"light": "#F7F8FA", "dark": "#1F272D"},
        "widgetBackgroundColor": {"light": "#FFFFFF", "dark": "#11171C"},
        "widgetBorderColor": {"light": "#FFFFFF", "dark": "#11171C"},
        "fontColor": {"light": "__PRIMARY2__", "dark": "#E8ECF0"},
        "selectionColor": {"light": "__SECONDARY__", "dark": "#8ACAFF"},
        "visualizationColors": ["__PRIMARY2__", "__ACCENT__", "__SECONDARY__", "#7A7FB8", "#9BD3A0", "#F2A93B", "#5BC0EB", "__PRIMARY__"],
        "widgetHeaderAlignment": "LEFT", "fontFamily": "__FONT__", "widgetCornerRadius": 10}},
}


def build(fq, name, company, site, colors, font="Inter"):
    """Return the serialized dashboard with every dataset fully qualified and pinned to the assistant schema."""
    catalog, schema = fq.split(".")
    for d in dash["datasets"]:
        d["catalog"], d["schema"] = catalog, schema
    out = json.dumps(dash, ensure_ascii=False)
    for k, v in {"__FQ__": fq, "__NAME__": name, "__COMPANY__": company, "__SITE__": site, "__PRIMARY__": colors["primary"],
                 "__PRIMARY2__": colors.get("primary_2", colors["primary"]), "__ACCENT__": colors["accent"],
                 "__SECONDARY__": colors.get("secondary", colors["accent"]), "__FONT__": font}.items():
        out = out.replace(k, v)
    return out
