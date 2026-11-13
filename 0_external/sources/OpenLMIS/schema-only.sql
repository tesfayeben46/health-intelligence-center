--
-- PostgreSQL database dump
--

\restrict 6VEzeZrEQhQqrUAfJZeAf2HqH5i7lhIPQadnBfNk2T7K5vCWkykruxVDthM5jsj

-- Dumped from database version 16.13
-- Dumped by pg_dump version 16.13

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: history; Type: SCHEMA; Schema: -; Owner: elmis
--

CREATE SCHEMA history;


ALTER SCHEMA history OWNER TO elmis;

--
-- Name: mutation; Type: SCHEMA; Schema: -; Owner: elmis
--

CREATE SCHEMA mutation;


ALTER SCHEMA mutation OWNER TO elmis;

--
-- Name: reference; Type: SCHEMA; Schema: -; Owner: elmis
--

CREATE SCHEMA reference;


ALTER SCHEMA reference OWNER TO elmis;

--
-- Name: reject_scenario_mutation(); Type: FUNCTION; Schema: mutation; Owner: elmis
--

CREATE FUNCTION mutation.reject_scenario_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
begin
  raise exception 'installed eLMIS mutation scenario is immutable: %.%',
    tg_table_schema, tg_table_name;
end
$$;


ALTER FUNCTION mutation.reject_scenario_mutation() OWNER TO elmis;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: requisition; Type: TABLE; Schema: history; Owner: elmis
--

CREATE TABLE history.requisition (
    source_requisition_id bigint NOT NULL,
    facility_code text NOT NULL,
    programme_code text NOT NULL,
    source_period_id bigint NOT NULL,
    status text NOT NULL,
    emergency boolean NOT NULL,
    source_application text NOT NULL,
    requisition_created_at timestamp without time zone,
    requisition_modified_at timestamp without time zone
);


ALTER TABLE history.requisition OWNER TO elmis;

--
-- Name: requisition_line; Type: TABLE; Schema: history; Owner: elmis
--

CREATE TABLE history.requisition_line (
    source_line_item_id bigint NOT NULL,
    source_requisition_id bigint NOT NULL,
    product_code text NOT NULL,
    reported_product_name text,
    reported_product_category text,
    reported_dispensing_unit text NOT NULL,
    beginning_balance integer,
    quantity_received integer,
    quantity_dispensed integer,
    stock_in_hand integer,
    quantity_requested integer,
    calculated_order_quantity integer,
    quantity_approved integer,
    total_losses_and_adjustments integer,
    new_patient_count integer,
    stockout_days integer,
    normalized_consumption integer,
    amc integer,
    max_months_of_stock integer NOT NULL,
    max_stock_quantity integer,
    packs_to_ship integer,
    doses_per_month integer NOT NULL,
    doses_per_dispensing_unit integer NOT NULL,
    pack_size smallint NOT NULL,
    round_to_zero boolean,
    pack_rounding_threshold integer,
    full_supply boolean NOT NULL,
    skipped boolean NOT NULL,
    reporting_days integer,
    previous_stock_in_hand integer,
    period_normalized_consumption integer,
    line_item_created_at timestamp without time zone,
    line_item_modified_at timestamp without time zone
);


ALTER TABLE history.requisition_line OWNER TO elmis;

--
-- Name: scenario_manifest; Type: TABLE; Schema: mutation; Owner: elmis
--

CREATE TABLE mutation.scenario_manifest (
    scenario_code text NOT NULL,
    contract_code text NOT NULL,
    contract_sha256 text NOT NULL,
    algorithm_version text NOT NULL,
    source_database text NOT NULL,
    source_baseline_uid uuid NOT NULL,
    seed_sha256 text NOT NULL,
    state text NOT NULL,
    contract jsonb NOT NULL,
    source_hashes_before jsonb NOT NULL,
    source_hashes_after jsonb DEFAULT '{}'::jsonb NOT NULL,
    target_hashes jsonb DEFAULT '{}'::jsonb NOT NULL,
    transformation_statistics jsonb DEFAULT '{}'::jsonb NOT NULL,
    validation_summary jsonb DEFAULT '{}'::jsonb NOT NULL,
    started_at timestamp with time zone DEFAULT transaction_timestamp() NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT scenario_manifest_check CHECK (((state = ANY (ARRAY['installed'::text, 'failed'::text])) = (completed_at IS NOT NULL))),
    CONSTRAINT scenario_manifest_contract_sha256_check CHECK ((length(contract_sha256) = 64)),
    CONSTRAINT scenario_manifest_seed_sha256_check CHECK ((length(seed_sha256) = 64)),
    CONSTRAINT scenario_manifest_state_check CHECK ((state = ANY (ARRAY['building'::text, 'validated'::text, 'installed'::text, 'failed'::text])))
);


ALTER TABLE mutation.scenario_manifest OWNER TO elmis;

--
-- Name: validation_metric; Type: TABLE; Schema: mutation; Owner: elmis
--

CREATE TABLE mutation.validation_metric (
    metric_code text NOT NULL,
    scope_code text NOT NULL,
    field_code text DEFAULT ''::text NOT NULL,
    source_value numeric,
    target_value numeric,
    distance numeric,
    threshold numeric,
    passed boolean NOT NULL,
    details jsonb DEFAULT '{}'::jsonb NOT NULL
);


ALTER TABLE mutation.validation_metric OWNER TO elmis;

--
-- Name: dosage_unit; Type: TABLE; Schema: reference; Owner: elmis
--

CREATE TABLE reference.dosage_unit (
    code text NOT NULL,
    source_id bigint NOT NULL,
    display_order integer
);


ALTER TABLE reference.dosage_unit OWNER TO elmis;

--
-- Name: facility; Type: TABLE; Schema: reference; Owner: elmis
--

CREATE TABLE reference.facility (
    code text NOT NULL,
    source_id bigint NOT NULL,
    name text NOT NULL,
    gln text,
    facility_type_code text NOT NULL,
    geographic_zone_code text NOT NULL,
    parent_facility_code text,
    catchment_population integer,
    latitude numeric,
    longitude numeric,
    cold_storage_gross_capacity numeric,
    cold_storage_net_capacity numeric,
    supplies_others boolean,
    service_delivery_point boolean NOT NULL,
    online boolean,
    satellite boolean,
    has_electricity boolean,
    active boolean NOT NULL,
    enabled boolean NOT NULL,
    virtual_facility boolean NOT NULL,
    go_live_date date NOT NULL,
    go_down_date date
);


ALTER TABLE reference.facility OWNER TO elmis;

--
-- Name: facility_programme_schedule; Type: TABLE; Schema: reference; Owner: elmis
--

CREATE TABLE reference.facility_programme_schedule (
    facility_code text NOT NULL,
    programme_code text NOT NULL,
    processing_schedule_code text NOT NULL,
    source_requisition_group_id bigint NOT NULL,
    source_requisition_group_code text NOT NULL,
    source_group_member_id bigint NOT NULL,
    source_group_schedule_id bigint NOT NULL,
    direct_delivery boolean NOT NULL,
    drop_off_facility_code text
);


ALTER TABLE reference.facility_programme_schedule OWNER TO elmis;

--
-- Name: facility_type; Type: TABLE; Schema: reference; Owner: elmis
--

CREATE TABLE reference.facility_type (
    code text NOT NULL,
    source_id bigint NOT NULL,
    name text NOT NULL,
    geographic_level_code text,
    nominal_max_months integer NOT NULL,
    nominal_eop numeric NOT NULL,
    active boolean
);


ALTER TABLE reference.facility_type OWNER TO elmis;

--
-- Name: geographic_level; Type: TABLE; Schema: reference; Owner: elmis
--

CREATE TABLE reference.geographic_level (
    code text NOT NULL,
    source_id bigint NOT NULL,
    name text NOT NULL,
    level_number integer NOT NULL
);


ALTER TABLE reference.geographic_level OWNER TO elmis;

--
-- Name: geographic_zone; Type: TABLE; Schema: reference; Owner: elmis
--

CREATE TABLE reference.geographic_zone (
    code text NOT NULL,
    source_id bigint NOT NULL,
    name text NOT NULL,
    geographic_level_code text NOT NULL,
    parent_zone_code text,
    catchment_population integer,
    latitude numeric,
    longitude numeric
);


ALTER TABLE reference.geographic_zone OWNER TO elmis;

--
-- Name: processing_schedule; Type: TABLE; Schema: reference; Owner: elmis
--

CREATE TABLE reference.processing_schedule (
    code text NOT NULL,
    source_id bigint NOT NULL,
    name text NOT NULL
);


ALTER TABLE reference.processing_schedule OWNER TO elmis;

--
-- Name: product; Type: TABLE; Schema: reference; Owner: elmis
--

CREATE TABLE reference.product (
    code text NOT NULL,
    source_id bigint NOT NULL,
    primary_name text NOT NULL,
    full_name text,
    generic_name text,
    strength text,
    product_form_code text,
    dosage_unit_code text,
    product_group_code text,
    dispensing_unit text NOT NULL,
    doses_per_dispensing_unit smallint NOT NULL,
    pack_size smallint NOT NULL,
    alternate_pack_size smallint,
    refrigerated boolean,
    room_temperature boolean,
    hazardous boolean,
    flammable boolean,
    controlled_substance boolean,
    light_sensitive boolean,
    source_pack_length numeric,
    source_pack_width numeric,
    source_pack_height numeric,
    source_pack_weight numeric,
    packs_per_carton smallint,
    source_carton_length numeric,
    source_carton_width numeric,
    source_carton_height numeric,
    cartons_per_pallet smallint,
    expected_shelf_life smallint,
    active boolean NOT NULL,
    full_supply boolean NOT NULL,
    tracer boolean NOT NULL,
    round_to_zero boolean NOT NULL,
    pack_rounding_threshold smallint NOT NULL,
    archived boolean
);


ALTER TABLE reference.product OWNER TO elmis;

--
-- Name: product_category; Type: TABLE; Schema: reference; Owner: elmis
--

CREATE TABLE reference.product_category (
    code text NOT NULL,
    source_id bigint NOT NULL,
    name text NOT NULL,
    display_order integer NOT NULL
);


ALTER TABLE reference.product_category OWNER TO elmis;

--
-- Name: product_form; Type: TABLE; Schema: reference; Owner: elmis
--

CREATE TABLE reference.product_form (
    code text NOT NULL,
    source_id bigint NOT NULL,
    display_order integer
);


ALTER TABLE reference.product_form OWNER TO elmis;

--
-- Name: product_group; Type: TABLE; Schema: reference; Owner: elmis
--

CREATE TABLE reference.product_group (
    code text NOT NULL,
    source_id bigint NOT NULL,
    name text NOT NULL
);


ALTER TABLE reference.product_group OWNER TO elmis;

--
-- Name: programme; Type: TABLE; Schema: reference; Owner: elmis
--

CREATE TABLE reference.programme (
    code text NOT NULL,
    source_id bigint NOT NULL,
    name text,
    active boolean,
    budgeting_applies boolean NOT NULL,
    uses_dar boolean,
    push boolean,
    enable_skip_period boolean NOT NULL,
    use_price_schedule boolean NOT NULL
);


ALTER TABLE reference.programme OWNER TO elmis;

--
-- Name: programme_product; Type: TABLE; Schema: reference; Owner: elmis
--

CREATE TABLE reference.programme_product (
    programme_code text NOT NULL,
    product_code text NOT NULL,
    source_id bigint NOT NULL,
    product_category_code text NOT NULL,
    doses_per_month integer NOT NULL,
    active boolean NOT NULL,
    current_price numeric,
    display_order integer,
    full_supply boolean
);


ALTER TABLE reference.programme_product OWNER TO elmis;

--
-- Name: reporting_period; Type: TABLE; Schema: reference; Owner: elmis
--

CREATE TABLE reference.reporting_period (
    source_id bigint NOT NULL,
    processing_schedule_code text NOT NULL,
    name text NOT NULL,
    period_start timestamp without time zone NOT NULL,
    period_end timestamp without time zone NOT NULL,
    number_of_months integer,
    CONSTRAINT reporting_period_check CHECK ((period_start <= period_end))
);


ALTER TABLE reference.reporting_period OWNER TO elmis;

--
-- Name: requisition_line requisition_line_pkey; Type: CONSTRAINT; Schema: history; Owner: elmis
--

ALTER TABLE ONLY history.requisition_line
    ADD CONSTRAINT requisition_line_pkey PRIMARY KEY (source_line_item_id);


--
-- Name: requisition requisition_pkey; Type: CONSTRAINT; Schema: history; Owner: elmis
--

ALTER TABLE ONLY history.requisition
    ADD CONSTRAINT requisition_pkey PRIMARY KEY (source_requisition_id);


--
-- Name: scenario_manifest scenario_manifest_pkey; Type: CONSTRAINT; Schema: mutation; Owner: elmis
--

ALTER TABLE ONLY mutation.scenario_manifest
    ADD CONSTRAINT scenario_manifest_pkey PRIMARY KEY (scenario_code);


--
-- Name: validation_metric validation_metric_pkey; Type: CONSTRAINT; Schema: mutation; Owner: elmis
--

ALTER TABLE ONLY mutation.validation_metric
    ADD CONSTRAINT validation_metric_pkey PRIMARY KEY (metric_code, scope_code, field_code);


--
-- Name: dosage_unit dosage_unit_pkey; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.dosage_unit
    ADD CONSTRAINT dosage_unit_pkey PRIMARY KEY (code);


--
-- Name: dosage_unit dosage_unit_source_id_key; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.dosage_unit
    ADD CONSTRAINT dosage_unit_source_id_key UNIQUE (source_id);


--
-- Name: facility facility_pkey; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.facility
    ADD CONSTRAINT facility_pkey PRIMARY KEY (code);


--
-- Name: facility_programme_schedule facility_programme_schedule_pkey; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.facility_programme_schedule
    ADD CONSTRAINT facility_programme_schedule_pkey PRIMARY KEY (facility_code, programme_code, processing_schedule_code);


--
-- Name: facility_programme_schedule facility_programme_schedule_source_group_member_id_source_g_key; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.facility_programme_schedule
    ADD CONSTRAINT facility_programme_schedule_source_group_member_id_source_g_key UNIQUE (source_group_member_id, source_group_schedule_id);


--
-- Name: facility facility_source_id_key; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.facility
    ADD CONSTRAINT facility_source_id_key UNIQUE (source_id);


--
-- Name: facility_type facility_type_pkey; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.facility_type
    ADD CONSTRAINT facility_type_pkey PRIMARY KEY (code);


--
-- Name: facility_type facility_type_source_id_key; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.facility_type
    ADD CONSTRAINT facility_type_source_id_key UNIQUE (source_id);


--
-- Name: geographic_level geographic_level_level_number_key; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.geographic_level
    ADD CONSTRAINT geographic_level_level_number_key UNIQUE (level_number);


--
-- Name: geographic_level geographic_level_pkey; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.geographic_level
    ADD CONSTRAINT geographic_level_pkey PRIMARY KEY (code);


--
-- Name: geographic_level geographic_level_source_id_key; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.geographic_level
    ADD CONSTRAINT geographic_level_source_id_key UNIQUE (source_id);


--
-- Name: geographic_zone geographic_zone_pkey; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.geographic_zone
    ADD CONSTRAINT geographic_zone_pkey PRIMARY KEY (code);


--
-- Name: geographic_zone geographic_zone_source_id_key; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.geographic_zone
    ADD CONSTRAINT geographic_zone_source_id_key UNIQUE (source_id);


--
-- Name: processing_schedule processing_schedule_pkey; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.processing_schedule
    ADD CONSTRAINT processing_schedule_pkey PRIMARY KEY (code);


--
-- Name: processing_schedule processing_schedule_source_id_key; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.processing_schedule
    ADD CONSTRAINT processing_schedule_source_id_key UNIQUE (source_id);


--
-- Name: product_category product_category_pkey; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.product_category
    ADD CONSTRAINT product_category_pkey PRIMARY KEY (code);


--
-- Name: product_category product_category_source_id_key; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.product_category
    ADD CONSTRAINT product_category_source_id_key UNIQUE (source_id);


--
-- Name: product_form product_form_pkey; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.product_form
    ADD CONSTRAINT product_form_pkey PRIMARY KEY (code);


--
-- Name: product_form product_form_source_id_key; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.product_form
    ADD CONSTRAINT product_form_source_id_key UNIQUE (source_id);


--
-- Name: product_group product_group_pkey; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.product_group
    ADD CONSTRAINT product_group_pkey PRIMARY KEY (code);


--
-- Name: product_group product_group_source_id_key; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.product_group
    ADD CONSTRAINT product_group_source_id_key UNIQUE (source_id);


--
-- Name: product product_pkey; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.product
    ADD CONSTRAINT product_pkey PRIMARY KEY (code);


--
-- Name: product product_source_id_key; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.product
    ADD CONSTRAINT product_source_id_key UNIQUE (source_id);


--
-- Name: programme programme_pkey; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.programme
    ADD CONSTRAINT programme_pkey PRIMARY KEY (code);


--
-- Name: programme_product programme_product_pkey; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.programme_product
    ADD CONSTRAINT programme_product_pkey PRIMARY KEY (programme_code, product_code);


--
-- Name: programme_product programme_product_source_id_key; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.programme_product
    ADD CONSTRAINT programme_product_source_id_key UNIQUE (source_id);


--
-- Name: programme programme_source_id_key; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.programme
    ADD CONSTRAINT programme_source_id_key UNIQUE (source_id);


--
-- Name: reporting_period reporting_period_pkey; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.reporting_period
    ADD CONSTRAINT reporting_period_pkey PRIMARY KEY (source_id);


--
-- Name: reporting_period reporting_period_processing_schedule_code_period_start_peri_key; Type: CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.reporting_period
    ADD CONSTRAINT reporting_period_processing_schedule_code_period_start_peri_key UNIQUE (processing_schedule_code, period_start, period_end);


--
-- Name: requisition_line_parent_idx; Type: INDEX; Schema: history; Owner: elmis
--

CREATE INDEX requisition_line_parent_idx ON history.requisition_line USING btree (source_requisition_id);


--
-- Name: requisition_line_product_idx; Type: INDEX; Schema: history; Owner: elmis
--

CREATE INDEX requisition_line_product_idx ON history.requisition_line USING btree (product_code, source_requisition_id);


--
-- Name: requisition_period_idx; Type: INDEX; Schema: history; Owner: elmis
--

CREATE INDEX requisition_period_idx ON history.requisition USING btree (source_period_id, programme_code, facility_code);


--
-- Name: requisition_line reject_requisition_line_mutation; Type: TRIGGER; Schema: history; Owner: elmis
--

CREATE TRIGGER reject_requisition_line_mutation BEFORE INSERT OR DELETE OR UPDATE OR TRUNCATE ON history.requisition_line FOR EACH STATEMENT EXECUTE FUNCTION mutation.reject_scenario_mutation();


--
-- Name: requisition reject_requisition_mutation; Type: TRIGGER; Schema: history; Owner: elmis
--

CREATE TRIGGER reject_requisition_mutation BEFORE INSERT OR DELETE OR UPDATE OR TRUNCATE ON history.requisition FOR EACH STATEMENT EXECUTE FUNCTION mutation.reject_scenario_mutation();


--
-- Name: scenario_manifest reject_scenario_manifest_mutation; Type: TRIGGER; Schema: mutation; Owner: elmis
--

CREATE TRIGGER reject_scenario_manifest_mutation BEFORE INSERT OR DELETE OR UPDATE OR TRUNCATE ON mutation.scenario_manifest FOR EACH STATEMENT EXECUTE FUNCTION mutation.reject_scenario_mutation();


--
-- Name: validation_metric reject_validation_metric_mutation; Type: TRIGGER; Schema: mutation; Owner: elmis
--

CREATE TRIGGER reject_validation_metric_mutation BEFORE INSERT OR DELETE OR UPDATE OR TRUNCATE ON mutation.validation_metric FOR EACH STATEMENT EXECUTE FUNCTION mutation.reject_scenario_mutation();


--
-- Name: dosage_unit reject_dosage_unit_mutation; Type: TRIGGER; Schema: reference; Owner: elmis
--

CREATE TRIGGER reject_dosage_unit_mutation BEFORE INSERT OR DELETE OR UPDATE OR TRUNCATE ON reference.dosage_unit FOR EACH STATEMENT EXECUTE FUNCTION mutation.reject_scenario_mutation();


--
-- Name: facility reject_facility_mutation; Type: TRIGGER; Schema: reference; Owner: elmis
--

CREATE TRIGGER reject_facility_mutation BEFORE INSERT OR DELETE OR UPDATE OR TRUNCATE ON reference.facility FOR EACH STATEMENT EXECUTE FUNCTION mutation.reject_scenario_mutation();


--
-- Name: facility_programme_schedule reject_facility_programme_schedule_mutation; Type: TRIGGER; Schema: reference; Owner: elmis
--

CREATE TRIGGER reject_facility_programme_schedule_mutation BEFORE INSERT OR DELETE OR UPDATE OR TRUNCATE ON reference.facility_programme_schedule FOR EACH STATEMENT EXECUTE FUNCTION mutation.reject_scenario_mutation();


--
-- Name: facility_type reject_facility_type_mutation; Type: TRIGGER; Schema: reference; Owner: elmis
--

CREATE TRIGGER reject_facility_type_mutation BEFORE INSERT OR DELETE OR UPDATE OR TRUNCATE ON reference.facility_type FOR EACH STATEMENT EXECUTE FUNCTION mutation.reject_scenario_mutation();


--
-- Name: geographic_level reject_geographic_level_mutation; Type: TRIGGER; Schema: reference; Owner: elmis
--

CREATE TRIGGER reject_geographic_level_mutation BEFORE INSERT OR DELETE OR UPDATE OR TRUNCATE ON reference.geographic_level FOR EACH STATEMENT EXECUTE FUNCTION mutation.reject_scenario_mutation();


--
-- Name: geographic_zone reject_geographic_zone_mutation; Type: TRIGGER; Schema: reference; Owner: elmis
--

CREATE TRIGGER reject_geographic_zone_mutation BEFORE INSERT OR DELETE OR UPDATE OR TRUNCATE ON reference.geographic_zone FOR EACH STATEMENT EXECUTE FUNCTION mutation.reject_scenario_mutation();


--
-- Name: processing_schedule reject_processing_schedule_mutation; Type: TRIGGER; Schema: reference; Owner: elmis
--

CREATE TRIGGER reject_processing_schedule_mutation BEFORE INSERT OR DELETE OR UPDATE OR TRUNCATE ON reference.processing_schedule FOR EACH STATEMENT EXECUTE FUNCTION mutation.reject_scenario_mutation();


--
-- Name: product_category reject_product_category_mutation; Type: TRIGGER; Schema: reference; Owner: elmis
--

CREATE TRIGGER reject_product_category_mutation BEFORE INSERT OR DELETE OR UPDATE OR TRUNCATE ON reference.product_category FOR EACH STATEMENT EXECUTE FUNCTION mutation.reject_scenario_mutation();


--
-- Name: product_form reject_product_form_mutation; Type: TRIGGER; Schema: reference; Owner: elmis
--

CREATE TRIGGER reject_product_form_mutation BEFORE INSERT OR DELETE OR UPDATE OR TRUNCATE ON reference.product_form FOR EACH STATEMENT EXECUTE FUNCTION mutation.reject_scenario_mutation();


--
-- Name: product_group reject_product_group_mutation; Type: TRIGGER; Schema: reference; Owner: elmis
--

CREATE TRIGGER reject_product_group_mutation BEFORE INSERT OR DELETE OR UPDATE OR TRUNCATE ON reference.product_group FOR EACH STATEMENT EXECUTE FUNCTION mutation.reject_scenario_mutation();


--
-- Name: product reject_product_mutation; Type: TRIGGER; Schema: reference; Owner: elmis
--

CREATE TRIGGER reject_product_mutation BEFORE INSERT OR DELETE OR UPDATE OR TRUNCATE ON reference.product FOR EACH STATEMENT EXECUTE FUNCTION mutation.reject_scenario_mutation();


--
-- Name: programme reject_programme_mutation; Type: TRIGGER; Schema: reference; Owner: elmis
--

CREATE TRIGGER reject_programme_mutation BEFORE INSERT OR DELETE OR UPDATE OR TRUNCATE ON reference.programme FOR EACH STATEMENT EXECUTE FUNCTION mutation.reject_scenario_mutation();


--
-- Name: programme_product reject_programme_product_mutation; Type: TRIGGER; Schema: reference; Owner: elmis
--

CREATE TRIGGER reject_programme_product_mutation BEFORE INSERT OR DELETE OR UPDATE OR TRUNCATE ON reference.programme_product FOR EACH STATEMENT EXECUTE FUNCTION mutation.reject_scenario_mutation();


--
-- Name: reporting_period reject_reporting_period_mutation; Type: TRIGGER; Schema: reference; Owner: elmis
--

CREATE TRIGGER reject_reporting_period_mutation BEFORE INSERT OR DELETE OR UPDATE OR TRUNCATE ON reference.reporting_period FOR EACH STATEMENT EXECUTE FUNCTION mutation.reject_scenario_mutation();


--
-- Name: requisition requisition_facility_code_fkey; Type: FK CONSTRAINT; Schema: history; Owner: elmis
--

ALTER TABLE ONLY history.requisition
    ADD CONSTRAINT requisition_facility_code_fkey FOREIGN KEY (facility_code) REFERENCES reference.facility(code);


--
-- Name: requisition_line requisition_line_product_code_fkey; Type: FK CONSTRAINT; Schema: history; Owner: elmis
--

ALTER TABLE ONLY history.requisition_line
    ADD CONSTRAINT requisition_line_product_code_fkey FOREIGN KEY (product_code) REFERENCES reference.product(code);


--
-- Name: requisition_line requisition_line_source_requisition_id_fkey; Type: FK CONSTRAINT; Schema: history; Owner: elmis
--

ALTER TABLE ONLY history.requisition_line
    ADD CONSTRAINT requisition_line_source_requisition_id_fkey FOREIGN KEY (source_requisition_id) REFERENCES history.requisition(source_requisition_id);


--
-- Name: requisition requisition_programme_code_fkey; Type: FK CONSTRAINT; Schema: history; Owner: elmis
--

ALTER TABLE ONLY history.requisition
    ADD CONSTRAINT requisition_programme_code_fkey FOREIGN KEY (programme_code) REFERENCES reference.programme(code);


--
-- Name: requisition requisition_source_period_id_fkey; Type: FK CONSTRAINT; Schema: history; Owner: elmis
--

ALTER TABLE ONLY history.requisition
    ADD CONSTRAINT requisition_source_period_id_fkey FOREIGN KEY (source_period_id) REFERENCES reference.reporting_period(source_id);


--
-- Name: facility facility_facility_type_code_fkey; Type: FK CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.facility
    ADD CONSTRAINT facility_facility_type_code_fkey FOREIGN KEY (facility_type_code) REFERENCES reference.facility_type(code);


--
-- Name: facility facility_geographic_zone_code_fkey; Type: FK CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.facility
    ADD CONSTRAINT facility_geographic_zone_code_fkey FOREIGN KEY (geographic_zone_code) REFERENCES reference.geographic_zone(code);


--
-- Name: facility facility_parent_facility_code_fkey; Type: FK CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.facility
    ADD CONSTRAINT facility_parent_facility_code_fkey FOREIGN KEY (parent_facility_code) REFERENCES reference.facility(code) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: facility_programme_schedule facility_programme_schedule_drop_off_facility_code_fkey; Type: FK CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.facility_programme_schedule
    ADD CONSTRAINT facility_programme_schedule_drop_off_facility_code_fkey FOREIGN KEY (drop_off_facility_code) REFERENCES reference.facility(code);


--
-- Name: facility_programme_schedule facility_programme_schedule_facility_code_fkey; Type: FK CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.facility_programme_schedule
    ADD CONSTRAINT facility_programme_schedule_facility_code_fkey FOREIGN KEY (facility_code) REFERENCES reference.facility(code);


--
-- Name: facility_programme_schedule facility_programme_schedule_processing_schedule_code_fkey; Type: FK CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.facility_programme_schedule
    ADD CONSTRAINT facility_programme_schedule_processing_schedule_code_fkey FOREIGN KEY (processing_schedule_code) REFERENCES reference.processing_schedule(code);


--
-- Name: facility_programme_schedule facility_programme_schedule_programme_code_fkey; Type: FK CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.facility_programme_schedule
    ADD CONSTRAINT facility_programme_schedule_programme_code_fkey FOREIGN KEY (programme_code) REFERENCES reference.programme(code);


--
-- Name: facility_type facility_type_geographic_level_code_fkey; Type: FK CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.facility_type
    ADD CONSTRAINT facility_type_geographic_level_code_fkey FOREIGN KEY (geographic_level_code) REFERENCES reference.geographic_level(code);


--
-- Name: geographic_zone geographic_zone_geographic_level_code_fkey; Type: FK CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.geographic_zone
    ADD CONSTRAINT geographic_zone_geographic_level_code_fkey FOREIGN KEY (geographic_level_code) REFERENCES reference.geographic_level(code);


--
-- Name: geographic_zone geographic_zone_parent_zone_code_fkey; Type: FK CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.geographic_zone
    ADD CONSTRAINT geographic_zone_parent_zone_code_fkey FOREIGN KEY (parent_zone_code) REFERENCES reference.geographic_zone(code) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: product product_dosage_unit_code_fkey; Type: FK CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.product
    ADD CONSTRAINT product_dosage_unit_code_fkey FOREIGN KEY (dosage_unit_code) REFERENCES reference.dosage_unit(code);


--
-- Name: product product_product_form_code_fkey; Type: FK CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.product
    ADD CONSTRAINT product_product_form_code_fkey FOREIGN KEY (product_form_code) REFERENCES reference.product_form(code);


--
-- Name: product product_product_group_code_fkey; Type: FK CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.product
    ADD CONSTRAINT product_product_group_code_fkey FOREIGN KEY (product_group_code) REFERENCES reference.product_group(code);


--
-- Name: programme_product programme_product_product_category_code_fkey; Type: FK CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.programme_product
    ADD CONSTRAINT programme_product_product_category_code_fkey FOREIGN KEY (product_category_code) REFERENCES reference.product_category(code);


--
-- Name: programme_product programme_product_product_code_fkey; Type: FK CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.programme_product
    ADD CONSTRAINT programme_product_product_code_fkey FOREIGN KEY (product_code) REFERENCES reference.product(code);


--
-- Name: programme_product programme_product_programme_code_fkey; Type: FK CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.programme_product
    ADD CONSTRAINT programme_product_programme_code_fkey FOREIGN KEY (programme_code) REFERENCES reference.programme(code);


--
-- Name: reporting_period reporting_period_processing_schedule_code_fkey; Type: FK CONSTRAINT; Schema: reference; Owner: elmis
--

ALTER TABLE ONLY reference.reporting_period
    ADD CONSTRAINT reporting_period_processing_schedule_code_fkey FOREIGN KEY (processing_schedule_code) REFERENCES reference.processing_schedule(code);


--
-- Name: SCHEMA history; Type: ACL; Schema: -; Owner: elmis
--

GRANT USAGE ON SCHEMA history TO elmis_reader;


--
-- Name: SCHEMA mutation; Type: ACL; Schema: -; Owner: elmis
--

GRANT USAGE ON SCHEMA mutation TO elmis_reader;


--
-- Name: SCHEMA reference; Type: ACL; Schema: -; Owner: elmis
--

GRANT USAGE ON SCHEMA reference TO elmis_reader;


--
-- Name: TABLE requisition; Type: ACL; Schema: history; Owner: elmis
--

GRANT SELECT ON TABLE history.requisition TO elmis_reader;


--
-- Name: TABLE requisition_line; Type: ACL; Schema: history; Owner: elmis
--

GRANT SELECT ON TABLE history.requisition_line TO elmis_reader;


--
-- Name: TABLE scenario_manifest; Type: ACL; Schema: mutation; Owner: elmis
--

GRANT SELECT ON TABLE mutation.scenario_manifest TO elmis_reader;


--
-- Name: TABLE validation_metric; Type: ACL; Schema: mutation; Owner: elmis
--

GRANT SELECT ON TABLE mutation.validation_metric TO elmis_reader;


--
-- Name: TABLE dosage_unit; Type: ACL; Schema: reference; Owner: elmis
--

GRANT SELECT ON TABLE reference.dosage_unit TO elmis_reader;


--
-- Name: TABLE facility; Type: ACL; Schema: reference; Owner: elmis
--

GRANT SELECT ON TABLE reference.facility TO elmis_reader;


--
-- Name: TABLE facility_programme_schedule; Type: ACL; Schema: reference; Owner: elmis
--

GRANT SELECT ON TABLE reference.facility_programme_schedule TO elmis_reader;


--
-- Name: TABLE facility_type; Type: ACL; Schema: reference; Owner: elmis
--

GRANT SELECT ON TABLE reference.facility_type TO elmis_reader;


--
-- Name: TABLE geographic_level; Type: ACL; Schema: reference; Owner: elmis
--

GRANT SELECT ON TABLE reference.geographic_level TO elmis_reader;


--
-- Name: TABLE geographic_zone; Type: ACL; Schema: reference; Owner: elmis
--

GRANT SELECT ON TABLE reference.geographic_zone TO elmis_reader;


--
-- Name: TABLE processing_schedule; Type: ACL; Schema: reference; Owner: elmis
--

GRANT SELECT ON TABLE reference.processing_schedule TO elmis_reader;


--
-- Name: TABLE product; Type: ACL; Schema: reference; Owner: elmis
--

GRANT SELECT ON TABLE reference.product TO elmis_reader;


--
-- Name: TABLE product_category; Type: ACL; Schema: reference; Owner: elmis
--

GRANT SELECT ON TABLE reference.product_category TO elmis_reader;


--
-- Name: TABLE product_form; Type: ACL; Schema: reference; Owner: elmis
--

GRANT SELECT ON TABLE reference.product_form TO elmis_reader;


--
-- Name: TABLE product_group; Type: ACL; Schema: reference; Owner: elmis
--

GRANT SELECT ON TABLE reference.product_group TO elmis_reader;


--
-- Name: TABLE programme; Type: ACL; Schema: reference; Owner: elmis
--

GRANT SELECT ON TABLE reference.programme TO elmis_reader;


--
-- Name: TABLE programme_product; Type: ACL; Schema: reference; Owner: elmis
--

GRANT SELECT ON TABLE reference.programme_product TO elmis_reader;


--
-- Name: TABLE reporting_period; Type: ACL; Schema: reference; Owner: elmis
--

GRANT SELECT ON TABLE reference.reporting_period TO elmis_reader;


--
-- Name: DEFAULT PRIVILEGES FOR TABLES; Type: DEFAULT ACL; Schema: history; Owner: elmis
--

ALTER DEFAULT PRIVILEGES FOR ROLE elmis IN SCHEMA history GRANT SELECT ON TABLES TO elmis_reader;


--
-- Name: DEFAULT PRIVILEGES FOR TABLES; Type: DEFAULT ACL; Schema: mutation; Owner: elmis
--

ALTER DEFAULT PRIVILEGES FOR ROLE elmis IN SCHEMA mutation GRANT SELECT ON TABLES TO elmis_reader;


--
-- Name: DEFAULT PRIVILEGES FOR TABLES; Type: DEFAULT ACL; Schema: reference; Owner: elmis
--

ALTER DEFAULT PRIVILEGES FOR ROLE elmis IN SCHEMA reference GRANT SELECT ON TABLES TO elmis_reader;


--
-- PostgreSQL database dump complete
--

\unrestrict 6VEzeZrEQhQqrUAfJZeAf2HqH5i7lhIPQadnBfNk2T7K5vCWkykruxVDthM5jsj

