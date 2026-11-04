SELECT customer_id, order_id, order_date, amount,
       row_number() OVER (PARTITION BY customer_id ORDER BY order_date, order_id) AS rn
FROM {{ ref('stg_orders') }}
