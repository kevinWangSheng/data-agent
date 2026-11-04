SELECT order_id, amount
FROM {{ ref('stg_orders') }}
{% if var('include_pending', false) %}
WHERE status IN ('pending', 'review')
{% endif %}
