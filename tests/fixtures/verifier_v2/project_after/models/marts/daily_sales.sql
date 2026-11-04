SELECT store_id, sale_date, revenue,
       sum(revenue) OVER (PARTITION BY store_id ORDER BY sale_date) AS running_revenue
FROM {{ source('raw', 'sales') }}
