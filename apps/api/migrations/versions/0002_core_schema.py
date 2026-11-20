"""Create the private core schema."""

from alembic import op

revision = "0002_core_schema"
down_revision = "0001_postgis"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE SCHEMA roghyar")
    op.execute("REVOKE ALL ON SCHEMA roghyar FROM PUBLIC")
    op.execute("SET LOCAL search_path = pg_catalog, public, extensions")
    op.execute("""
        CREATE TABLE roghyar.audit_log (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            actor UUID,
            actor_db_role VARCHAR(63),
            action TEXT NOT NULL,
            entity TEXT NOT NULL,
            entity_id UUID NOT NULL,
            at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id)
        )
    """)
    op.execute("""
        CREATE INDEX ix_audit_entity_at ON roghyar.audit_log (entity, entity_id, at)
    """)
    op.execute("""
        CREATE TABLE roghyar.formulary (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            condition_code TEXT NOT NULL,
            medicine TEXT NOT NULL,
            adult_dose TEXT,
            frequency TEXT,
            max_daily TEXT,
            min_age INTEGER,
            max_age INTEGER,
            child_dose_table JSONB,
            contraindications TEXT[],
            home_steps TEXT[],
            see_doctor_if TEXT[],
            version INTEGER NOT NULL,
            review_status TEXT DEFAULT 'draft' NOT NULL,
            reviewed_by UUID,
            reviewed_at TIMESTAMP WITH TIME ZONE,
            active BOOLEAN DEFAULT false NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            CHECK (version > 0),
            CHECK (review_status IN ('draft','approved','rejected')),
            CHECK (review_status = 'draft' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)),
            CHECK (NOT active OR review_status = 'approved'),
            CHECK (min_age BETWEEN 0 AND 120 AND max_age BETWEEN 0 AND 120 AND min_age <= max_age),
            UNIQUE (condition_code, medicine, version)
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX ux_formulary_active_version ON roghyar.formulary (condition_code, medicine) WHERE active
    """)
    op.execute("""
        CREATE TABLE roghyar.hospitals (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            name TEXT NOT NULL,
            aliases TEXT[],
            address TEXT NOT NULL,
            city TEXT NOT NULL,
            district TEXT NOT NULL,
            province TEXT NOT NULL,
            location geography(Point,4326) NOT NULL,
            phone TEXT,
            is_emergency_24_7 BOOLEAN DEFAULT false NOT NULL,
            departments TEXT[],
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            verified BOOLEAN DEFAULT false NOT NULL,
            published BOOLEAN DEFAULT false NOT NULL,
            verified_on TIMESTAMP WITH TIME ZONE,
            verified_by UUID,
            source TEXT,
            PRIMARY KEY (id),
            CHECK (NOT published OR verified),
            CHECK (NOT verified OR (verified_on IS NOT NULL AND verified_by IS NOT NULL AND source IS NOT NULL AND length(trim(source)) > 0))
        )
    """)
    op.execute("""
        CREATE INDEX ix_hospitals_city_name ON roghyar.hospitals (city, lower(name))
    """)
    op.execute("""
        CREATE INDEX ix_hospitals_location ON roghyar.hospitals USING gist (location)
    """)
    op.execute("""
        CREATE TABLE roghyar.otp_codes (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            phone_lookup BYTEA NOT NULL,
            code_hash BYTEA NOT NULL,
            purpose TEXT NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            consumed_at TIMESTAMP WITH TIME ZONE,
            attempts INTEGER DEFAULT 0 NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            CHECK (attempts >= 0 AND expires_at > created_at),
            CHECK (purpose IN ('booking', 'appointments')),
            CHECK (octet_length(phone_lookup) = 32)
        )
    """)
    op.execute("""
        CREATE INDEX ix_otp_lookup_purpose ON roghyar.otp_codes (phone_lookup, purpose, expires_at)
    """)
    op.execute("""
        CREATE TABLE roghyar.patients (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            auth_subject UUID NOT NULL,
            full_name_ciphertext BYTEA NOT NULL,
            phone_ciphertext BYTEA NOT NULL,
            phone_lookup BYTEA NOT NULL,
            phone_verified_at TIMESTAMP WITH TIME ZONE,
            guardian_name_ciphertext BYTEA,
            age INTEGER NOT NULL,
            gender TEXT,
            consent_at TIMESTAMP WITH TIME ZONE NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            CHECK (age BETWEEN 0 AND 120),
            CHECK (octet_length(phone_lookup) = 32),
            UNIQUE (auth_subject),
            UNIQUE (phone_lookup)
        )
    """)
    op.execute("""
        CREATE TABLE roghyar.red_flag_rules (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            rule_code TEXT NOT NULL,
            category TEXT NOT NULL,
            pattern JSONB NOT NULL,
            level TEXT NOT NULL,
            version INTEGER NOT NULL,
            review_status TEXT DEFAULT 'draft' NOT NULL,
            reviewed_by UUID,
            reviewed_at TIMESTAMP WITH TIME ZONE,
            active BOOLEAN DEFAULT false NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            CHECK (version > 0),
            CHECK (review_status IN ('draft','approved','rejected')),
            CHECK (review_status = 'draft' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)),
            CHECK (NOT active OR review_status = 'approved'),
            UNIQUE (rule_code, version)
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX ux_red_flag_active_version ON roghyar.red_flag_rules (rule_code) WHERE active
    """)
    op.execute("""
        CREATE TABLE roghyar.admin_users (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            hospital_id UUID NOT NULL,
            auth_subject UUID NOT NULL,
            email TEXT NOT NULL,
            role TEXT NOT NULL,
            active BOOLEAN DEFAULT false NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            UNIQUE (auth_subject, hospital_id),
            CHECK (role IN ('hospital_admin')),
            FOREIGN KEY(hospital_id) REFERENCES roghyar.hospitals (id)
        )
    """)
    op.execute("""
        CREATE TABLE roghyar.assessments (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            session_id UUID,
            patient_id UUID,
            text_ciphertext BYTEA,
            language VARCHAR(8),
            severity TEXT,
            duration_hours INTEGER,
            more_than_one_year BOOLEAN DEFAULT false NOT NULL,
            age INTEGER,
            answers_ciphertext BYTEA,
            safety_details_ciphertext BYTEA,
            level TEXT,
            condition_code TEXT,
            specialty TEXT,
            red_flags TEXT[],
            anonymized_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            CHECK (age BETWEEN 0 AND 120),
            CHECK (severity IN ('mild', 'moderate', 'severe')),
            CONSTRAINT ck_assessments_live_or_anonymized CHECK ((
               anonymized_at IS NULL AND (session_id IS NOT NULL OR patient_id IS NOT NULL)
               AND text_ciphertext IS NOT NULL AND age IS NOT NULL AND severity IS NOT NULL
               AND ((duration_hours IS NULL AND more_than_one_year)
                    OR (duration_hours BETWEEN 1 AND 8760 AND duration_hours IS NOT NULL
                        AND NOT more_than_one_year))
            ) OR (
               anonymized_at IS NOT NULL AND session_id IS NULL AND patient_id IS NULL
               AND text_ciphertext IS NULL AND age IS NULL AND severity IS NULL AND duration_hours IS NULL
               AND NOT more_than_one_year AND language IS NULL AND answers_ciphertext IS NULL
               AND safety_details_ciphertext IS NULL AND level IS NULL AND condition_code IS NULL
               AND specialty IS NULL AND red_flags IS NULL
            )),
            FOREIGN KEY(patient_id) REFERENCES roghyar.patients (id)
        )
    """)
    op.execute("""
        CREATE INDEX ix_assessments_patient ON roghyar.assessments (patient_id)
    """)
    op.execute("""
        CREATE INDEX ix_assessments_retention ON roghyar.assessments (created_at) WHERE anonymized_at IS NULL
    """)
    op.execute("""
        CREATE INDEX ix_assessments_session ON roghyar.assessments (session_id)
    """)
    op.execute("""
        CREATE TABLE roghyar.chat_messages (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            session_id UUID NOT NULL,
            patient_id UUID,
            role TEXT NOT NULL,
            text_ciphertext BYTEA NOT NULL,
            language VARCHAR(8),
            relevance_category TEXT,
            confidence NUMERIC(5, 4),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            CHECK (role IN ('patient', 'assistant')),
            CHECK (confidence BETWEEN 0 AND 1),
            FOREIGN KEY(patient_id) REFERENCES roghyar.patients (id)
        )
    """)
    op.execute("""
        CREATE INDEX ix_chat_patient ON roghyar.chat_messages (patient_id)
    """)
    op.execute("""
        CREATE INDEX ix_chat_session_created ON roghyar.chat_messages (session_id, created_at)
    """)
    op.execute("""
        CREATE TABLE roghyar.doctors (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            hospital_id UUID NOT NULL,
            full_name TEXT NOT NULL,
            specialty TEXT NOT NULL,
            qualifications TEXT[],
            experience_years INTEGER,
            bio TEXT,
            pmdc_reg_no VARCHAR(50) NOT NULL,
            fee NUMERIC(10, 2),
            languages TEXT[],
            photo_url TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            verified BOOLEAN DEFAULT false NOT NULL,
            published BOOLEAN DEFAULT false NOT NULL,
            verified_on TIMESTAMP WITH TIME ZONE,
            verified_by UUID,
            source TEXT,
            PRIMARY KEY (id),
            CHECK (NOT published OR verified),
            CHECK (NOT verified OR (verified_on IS NOT NULL AND verified_by IS NOT NULL AND source IS NOT NULL AND length(trim(source)) > 0)),
            UNIQUE (id, hospital_id),
            CHECK (experience_years >= 0),
            CHECK (fee >= 0),
            CHECK (length(trim(pmdc_reg_no)) > 0),
            FOREIGN KEY(hospital_id) REFERENCES roghyar.hospitals (id)
        )
    """)
    op.execute("""
        CREATE INDEX ix_doctors_hospital_specialty ON roghyar.doctors (hospital_id, specialty)
    """)
    op.execute("""
        CREATE UNIQUE INDEX ux_doctors_pmdc ON roghyar.doctors (lower(trim(pmdc_reg_no)))
    """)
    op.execute("""
        CREATE TABLE roghyar.appointments (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            booking_code VARCHAR(32) NOT NULL,
            doctor_id UUID NOT NULL,
            hospital_id UUID NOT NULL,
            patient_id UUID NOT NULL,
            assessment_id UUID,
            slot_start TIMESTAMP WITH TIME ZONE NOT NULL,
            slot_end TIMESTAMP WITH TIME ZONE NOT NULL,
            reason_ciphertext BYTEA,
            status TEXT NOT NULL,
            status_changed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            consent_at TIMESTAMP WITH TIME ZONE NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            FOREIGN KEY(doctor_id, hospital_id) REFERENCES roghyar.doctors (id, hospital_id),
            CHECK (slot_end > slot_start),
            CHECK (status IN ('pending','confirmed','cancelled','rescheduled','completed','no_show')),
            UNIQUE (booking_code),
            FOREIGN KEY(patient_id) REFERENCES roghyar.patients (id),
            FOREIGN KEY(assessment_id) REFERENCES roghyar.assessments (id)
        )
    """)
    op.execute("""
        CREATE INDEX ix_appointments_assessment ON roghyar.appointments (assessment_id)
    """)
    op.execute("""
        CREATE INDEX ix_appointments_hospital_start ON roghyar.appointments (hospital_id, slot_start)
    """)
    op.execute("""
        CREATE INDEX ix_appointments_patient_start ON roghyar.appointments (patient_id, slot_start)
    """)
    op.execute("""
        CREATE UNIQUE INDEX ux_appointments_active_slot ON roghyar.appointments (doctor_id, slot_start) WHERE status IN ('pending', 'confirmed')
    """)
    op.execute("""
        CREATE TABLE roghyar.doctor_schedules (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            doctor_id UUID NOT NULL,
            weekday INTEGER NOT NULL,
            start_time TIME WITHOUT TIME ZONE NOT NULL,
            end_time TIME WITHOUT TIME ZONE NOT NULL,
            slot_minutes INTEGER NOT NULL,
            location_note TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            CHECK (weekday BETWEEN 0 AND 6),
            CHECK (end_time > start_time AND slot_minutes BETWEEN 1 AND 720 AND extract(epoch FROM end_time - start_time) >= slot_minutes * 60),
            UNIQUE (doctor_id, weekday, start_time),
            FOREIGN KEY(doctor_id) REFERENCES roghyar.doctors (id)
        )
    """)


def downgrade():
    op.drop_table("doctor_schedules", schema="roghyar")
    op.drop_table("appointments", schema="roghyar")
    op.drop_table("doctors", schema="roghyar")
    op.drop_table("chat_messages", schema="roghyar")
    op.drop_table("assessments", schema="roghyar")
    op.drop_table("admin_users", schema="roghyar")
    op.drop_table("red_flag_rules", schema="roghyar")
    op.drop_table("patients", schema="roghyar")
    op.drop_table("otp_codes", schema="roghyar")
    op.drop_table("hospitals", schema="roghyar")
    op.drop_table("formulary", schema="roghyar")
    op.drop_table("audit_log", schema="roghyar")
    op.execute("DROP SCHEMA roghyar")
