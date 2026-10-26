-- Isolated compile fixtures from the frozen holdout requests; no model output used.
-- These objects are created only inside the compiler-owned disposable K7P_APP.
CREATE TABLE warehouses (warehouse_id NUMBER PRIMARY KEY, label VARCHAR2(80));
CREATE TABLE shipments (shipment_id NUMBER PRIMARY KEY, warehouse_id NUMBER,
  status VARCHAR2(12), created_at DATE, route_id NUMBER, destination_zone VARCHAR2(80));
CREATE TABLE ticket_receipts (ticket_no NUMBER NOT NULL, received_at DATE);
CREATE TABLE parcels (parcel_id NUMBER PRIMARY KEY, route_id NUMBER);
CREATE TABLE route_labels (route_id NUMBER PRIMARY KEY, label VARCHAR2(80) NOT NULL);
CREATE OR REPLACE FUNCTION clamp_nonnegative(p_value NUMBER) RETURN NUMBER IS
BEGIN
  RETURN CASE WHEN p_value < 0 THEN 0 ELSE p_value END;
END clamp_nonnegative;
/
