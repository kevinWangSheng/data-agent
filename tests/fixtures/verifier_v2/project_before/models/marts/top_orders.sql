SELECT order_id, amount
FROM {{ ref('stg_orders') }}
ORDER BY amount DESC
