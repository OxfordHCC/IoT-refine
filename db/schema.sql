--categories table matches readable descriptions to bursts of traffic ("this burst is a weather info request")
drop table if exists categories cascade;
create table categories (
	id SERIAL primary key,
	name varchar(40) not null -- e.g. "Alexa-time" or "Alexa-joke"
);

--collates bursts of traffic and optionally assigns them a category
drop table if exists bursts cascade;
create table bursts (
	id SERIAL primary key,
	category integer references categories --primary key assumed when no column given
);

--store core packet info, and optionally which burst it is part ofi, and which company it represents
drop table if exists packets cascade;
create table packets (
	id SERIAL primary key,
	time timestamp not null,
	src varchar(15) not null, --ip address of sending host
	dst varchar(15) not null, --ip address of receiving host
	mac varchar(17) not null, --mac address of internal host
	len integer not null, --packet length in bytes
	proto varchar(10) not null, --protocol if known, otherwise port number
	ext varchar(15) not null --external ip address (either src or dst)
);

-- create two indexes on src and dst to speed up lookups by these cols by loop.py
create index on packets (src);
create index on packets (dst);
create index on packets (time);

drop table if exists devices cascade;
create table devices(
	mac varchar(17) primary key,
	manufacturer varchar(40),
	name varchar(255) DEFAULT 'unknown'
);

drop table if exists geodata cascade;
create table geodata(
	ip varchar(15) primary key,
	lat real not null,
	lon real not null,
	c_code varchar(2) not null,
	c_name varchar(20) not null,
	domain varchar(30) not null
);

--firewall rules created by aretha
drop table if exists rules cascade;
create table rules(
	id SERIAL primary key,
	device varchar(17), --optional device to block traffic from (otherwise all devices)
	c_name varchar(20) not null --so that other matching ips can be blocked in future
);

--ip addresses blocked by aretha
drop table if exists blocked_ips cascade;
create table blocked_ips(
	id SERIAL primary key,
	ip varchar(15) not null,
	rule integer not null references rules on delete cascade
);

--beacon responses received from deployed research equipment
drop table if exists beacon;
create table beacon(
	id SERIAL primary key,
	source int not null,
	packets integer not null,
	geodata integer not null,
	firewall integer not null,
	time timestamp default current_timestamp
);

--questions to ask during studies
drop table if exists questions;
create table questions(
	id SERIAL primary key,
	concept varchar(200) not null,
	explanation varchar(500) not null,
	question varchar(200) not null,
	answer varchar(500),
	correct boolean,
	time timestamp default current_timestamp
);

--load questions
insert into questions(id, concept, explanation, question) values(
	1, 'Internet Trackers', 'A description', 'A question' --sample for now
);

drop table if exists experiment;
create table experiment(
	name varchar(10) primary key,
	value varchar(100) not null
);

--load initial values
insert into experiment(name, value) values('stage', 1);

drop materialized view if exists impacts;
create materialized view impacts as
	select mac, ext, round(extract(epoch from time)/60) as mins, sum(len) as impact
	from packets
	group by mac, ext, mins
	order by mins
with data;

drop function if exists notify_trigger();
CREATE FUNCTION notify_trigger() RETURNS trigger AS $trigger$
DECLARE
  rec RECORD;
  payload TEXT;
  column_name TEXT;
  column_value TEXT;
  payload_items JSONB;
BEGIN
  -- Set record row depending on operation
  CASE TG_OP
  WHEN 'INSERT', 'UPDATE' THEN
     rec := NEW;
  WHEN 'DELETE' THEN
     rec := OLD;
  ELSE
     RAISE EXCEPTION 'Unknown TG_OP: "%". Should not occur!', TG_OP;
  END CASE;
  
  -- Get required fields
  FOREACH column_name IN ARRAY TG_ARGV LOOP
    EXECUTE format('SELECT $1.%I::TEXT', column_name)
    INTO column_value
    USING rec;
    payload_items := coalesce(payload_items,'{}')::jsonb || json_build_object(column_name,column_value)::jsonb;
  END LOOP;

  -- Build the payload
  payload := json_build_object(
    'timestamp',CURRENT_TIMESTAMP,
    'operation',TG_OP,
    'schema',TG_TABLE_SCHEMA,
    'table',TG_TABLE_NAME,
    'data',payload_items
  );

  -- Notify the channel
  PERFORM pg_notify('db_notifications', payload);
  
  RETURN rec;
END;
$trigger$ LANGUAGE plpgsql;

drop trigger if exists packets_notify on packets;
create trigger packets_notify after insert or update or delete on packets
for each row execute procedure notify_trigger(
  'mac',
  'ext',
  'len'
);


drop trigger if exists device_notify on devices;
CREATE TRIGGER device_notify AFTER INSERT OR UPDATE OR DELETE ON devices
FOR EACH ROW EXECUTE PROCEDURE notify_trigger(
  'mac',
  'manufacturer',
  'name'
);

drop trigger if exists geodata_notify on geodata;
CREATE TRIGGER geodata_notify AFTER INSERT OR UPDATE OR DELETE ON geodata
FOR EACH ROW EXECUTE PROCEDURE notify_trigger(
  'ip',
  'lat',
  'lon',
  'c_code',
  'c_name'
);

