"""Point of sale: the shop counter of the surf school.

Wax, leashes, sunscreen, rash vests and cold drinks are sold across the desk all
day, usually while somebody is waiting to get in the water. Three ideas hold the
module together:

* **The shelf price is the price.** ``Product.sale_price`` is what the customer
  hands over — tax included. The tax component is derived, never added on top,
  so the number on the sticker, the number on the screen and the number in the
  till always agree.
* **Stock is a ledger.** :class:`~apps.pos.models.StockMovement` rows are
  append-only; ``Product.stock_quantity`` is a cache recomputed from them. A
  mistake is corrected by writing a compensating row, never by editing history.
* **Nothing is deleted.** A wrong sale is voided, which reverses the stock with
  new movements and leaves the original sale readable forever.
"""
