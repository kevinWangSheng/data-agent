## dbt Project Scan: dbt_shop

Do NOT run `dbt deps` — packages are pre-installed.

MODELS TO BUILD (defined in YML but no SQL file — create EXACT filenames):
  → aging_buckets.sql  (materialized=table)  YML: models/finance/aging/_finance_aging.yml
    Read sibling YML descriptions for column vocabulary hints
  → aging_summary.sql  (materialized=table)  YML: models/finance/aging/_finance_aging.yml
    Read sibling YML descriptions for column vocabulary hints
  → dim_accounts.sql  (materialized=view)  YML: models/finance/_finance.yml
    Read sibling YML descriptions for column vocabulary hints
  → dim_campaigns.sql  (materialized=view)  YML: models/marketing/_marketing.yml
    Read sibling YML descriptions for column vocabulary hints
  → fct_clicks.sql  (materialized=table)  YML: models/marketing/_marketing.yml
    Read sibling YML descriptions for column vocabulary hints
  → fct_invoices.sql  (materialized=table)  YML: models/finance/_finance.yml
    Read sibling YML descriptions for column vocabulary hints
  → fct_payments.sql  (materialized=table)  YML: models/finance/_finance.yml
    Read sibling YML descriptions for column vocabulary hints

STUBS TO REWRITE (SQL file exists but is incomplete):
  none

COMPLETE MODELS (have a non-stub SQL file — call list_tables to confirm which are materialized):
  customer_orders, stg_customers, stg_orders
  ⚠ These SQL files already exist and compile. Do NOT delete or recreate them.

SIBLING PATTERNS (complete models in same directory — read for column conventions):
  stg_customers: stg_orders (5 cols)
  stg_orders: stg_customers (4 cols)

REFERENCING MODELS (models that ref() these — read for column conventions):
  stg_customers is referenced by: customer_orders
  stg_orders is referenced by: customer_orders

DEPENDENCIES: (check YML refs and existing SQL for dependency info)

REQUIRED COLUMNS (the YML contract — match these names exactly):
  aging_buckets: aging_buckets_id | DESC: The aging_buckets model.
  aging_summary: aging_summary_id | DESC: The aging_summary model.
  dim_accounts: dim_accounts_id | DESC: The dim_accounts model.
  dim_campaigns: dim_campaigns_id | DESC: The dim_campaigns model.
  fct_clicks: fct_clicks_id | DESC: The fct_clicks model.
  fct_invoices: fct_invoices_id | DESC: The fct_invoices model.
  fct_payments: fct_payments_id | DESC: The fct_payments model.

AVAILABLE SOURCES:
  source('raw', '<table>') — tables: customers, orders, order_items, web_events

DB HINTS: use list_tables and query_database to check lookup joins, staging-vs-raw gaps, and parent-child driving tables.
