SELECT store_id, return_date, qty
FROM {{ source('raw', 'returns') }}
