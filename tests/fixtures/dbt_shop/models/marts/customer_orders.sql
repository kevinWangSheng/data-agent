SELECT c.customer_id, c.customer_name, count(o.order_id) AS n_orders,
       coalesce(sum(o.amount), 0) AS total_amount
FROM {{ ref('stg_customers') }} c
LEFT JOIN {{ ref('stg_orders') }} o ON o.customer_id = c.customer_id
GROUP BY c.customer_id, c.customer_name
