-- SQLite schema only. Use Alembic migrations for PostgreSQL.
CREATE TABLE account_grants (
	account_id VARCHAR(64) NOT NULL,
	member_id VARCHAR(64) NOT NULL,
	permission VARCHAR(16) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (account_id, member_id),
	CONSTRAINT ck_account_grants_permission CHECK (permission IN ('view','manage')),
	FOREIGN KEY(account_id) REFERENCES accounts (id) ON DELETE CASCADE,
	FOREIGN KEY(member_id) REFERENCES members (id) ON DELETE CASCADE
);

CREATE TABLE "accounts" (
	id VARCHAR(64) NOT NULL,
	name VARCHAR(120) NOT NULL,
	kind VARCHAR(16) NOT NULL,
	scope VARCHAR(16) NOT NULL,
	owner_id VARCHAR(64),
	currency VARCHAR(3) NOT NULL,
	opening_balance_minor BIGINT NOT NULL,
	icon VARCHAR(80) NOT NULL,
	version INTEGER NOT NULL,
	deleted_at DATETIME,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_accounts_scope CHECK (scope IN ('personal','family')),
	CONSTRAINT ck_accounts_kind CHECK (kind IN ('asset','liability')),
	CONSTRAINT ck_accounts_owner CHECK ((scope = 'personal' AND owner_id IS NOT NULL) OR (scope = 'family')),
	CONSTRAINT ck_accounts_opening_balance_safe CHECK (opening_balance_minor BETWEEN -9007199254740991 AND 9007199254740991),
	FOREIGN KEY(owner_id) REFERENCES members (id) ON DELETE RESTRICT
);

CREATE TABLE alembic_version (
	version_num VARCHAR(32) NOT NULL,
	CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

CREATE TABLE audit_events (
	id VARCHAR(64) NOT NULL,
	actor_id VARCHAR(64),
	action VARCHAR(100) NOT NULL,
	entity_type VARCHAR(80) NOT NULL,
	entity_id VARCHAR(64) NOT NULL,
	"before" JSON,
	"after" JSON,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(actor_id) REFERENCES members (id) ON DELETE SET NULL
);

CREATE TABLE backup_records (
	id VARCHAR(64) NOT NULL,
	created_at DATETIME NOT NULL,
	size_bytes BIGINT NOT NULL,
	encrypted BOOLEAN NOT NULL,
	status VARCHAR(16) NOT NULL,
	sha256 VARCHAR(64),
	encrypted_path TEXT,
	locations JSON NOT NULL,
	last_verified_at DATETIME,
	PRIMARY KEY (id),
	CONSTRAINT ck_backup_status CHECK (status IN ('creating','ready','failed'))
);

CREATE TABLE "categories" (
	id VARCHAR(64) NOT NULL,
	name VARCHAR(100) NOT NULL,
	kind VARCHAR(16) NOT NULL,
	parent_id VARCHAR(64),
	icon VARCHAR(80) NOT NULL,
	active BOOLEAN NOT NULL,
	version INTEGER NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_categories_kind CHECK (kind IN ('expense','income')),
	FOREIGN KEY(parent_id) REFERENCES categories (id) ON DELETE RESTRICT
);

CREATE TABLE "entry_lines" (
	id VARCHAR(64) NOT NULL,
	transaction_id VARCHAR(64) NOT NULL,
	account_id VARCHAR(64),
	ledger_code VARCHAR(160),
	debit_minor BIGINT NOT NULL,
	credit_minor BIGINT NOT NULL,
	account_amount_minor BIGINT,
	PRIMARY KEY (id),
	CONSTRAINT ck_entry_lines_one_side CHECK ((debit_minor > 0 AND credit_minor = 0) OR (credit_minor > 0 AND debit_minor = 0)),
	CONSTRAINT ck_entry_lines_account_amount CHECK ((account_id IS NOT NULL AND account_amount_minor > 0) OR (account_id IS NULL AND account_amount_minor IS NULL)),
	CONSTRAINT ck_entry_lines_target CHECK ((account_id IS NOT NULL AND ledger_code IS NULL) OR (account_id IS NULL AND ledger_code IS NOT NULL)),
	CONSTRAINT ck_entry_lines_nonnegative CHECK (debit_minor >= 0 AND credit_minor >= 0),
	CONSTRAINT ck_entry_lines_amounts_safe CHECK (debit_minor <= 9007199254740991 AND credit_minor <= 9007199254740991),
	CONSTRAINT ck_entry_lines_account_amount_safe CHECK (account_amount_minor IS NULL OR account_amount_minor <= 9007199254740991),
	FOREIGN KEY(transaction_id) REFERENCES transactions (id) ON DELETE CASCADE,
	FOREIGN KEY(account_id) REFERENCES accounts (id) ON DELETE RESTRICT
);

CREATE TABLE import_batches (
	id VARCHAR(64) NOT NULL,
	owner_id VARCHAR(64) NOT NULL,
	target_account_id VARCHAR(64),
	filename VARCHAR(300) NOT NULL,
	source VARCHAR(80) NOT NULL,
	imported_at DATETIME NOT NULL,
	status VARCHAR(24) NOT NULL,
	job_id VARCHAR(80),
	job_progress INTEGER NOT NULL,
	job_message VARCHAR(500) NOT NULL,
	processed_at DATETIME,
	version INTEGER NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_import_batches_status CHECK (status IN ('queued','processing','reviewing','needs_ocr','rejected','failed','posted','undone')),
	CONSTRAINT ck_import_batches_progress CHECK (job_progress >= 0 AND job_progress <= 100),
	FOREIGN KEY(owner_id) REFERENCES members (id) ON DELETE RESTRICT,
	FOREIGN KEY(target_account_id) REFERENCES accounts (id) ON DELETE RESTRICT
);

CREATE TABLE "import_candidates" (
	id VARCHAR(64) NOT NULL,
	batch_id VARCHAR(64) NOT NULL,
	source_record_id VARCHAR(64),
	date DATETIME NOT NULL,
	payee VARCHAR(300) NOT NULL,
	direction VARCHAR(16) NOT NULL,
	amount_minor BIGINT NOT NULL,
	currency VARCHAR(3) NOT NULL,
	suggested_category_id VARCHAR(64),
	original_category VARCHAR(160),
	target_account_id VARCHAR(64),
	duplicate_of_txn_id VARCHAR(64),
	status VARCHAR(16) NOT NULL,
	validation_status VARCHAR(24) NOT NULL,
	confidence NUMERIC(5, 4),
	transaction_id VARCHAR(64),
	version INTEGER NOT NULL,
	fx_rate NUMERIC(20, 10),
	base_amount_minor BIGINT,
	PRIMARY KEY (id),
	CONSTRAINT ck_import_candidates_base_amount CHECK (base_amount_minor IS NULL OR base_amount_minor > 0),
	CONSTRAINT ck_import_candidates_status CHECK (status IN ('pending','confirmed','skipped')),
	CONSTRAINT ck_import_candidates_direction CHECK (direction IN ('expense','income')),
	CONSTRAINT ck_import_candidates_amount CHECK (amount_minor > 0),
	CONSTRAINT ck_import_candidates_fx_rate CHECK (fx_rate IS NULL OR fx_rate > 0),
	CONSTRAINT ck_import_candidates_amount_safe CHECK (amount_minor <= 9007199254740991),
	CONSTRAINT ck_import_candidates_base_amount_safe CHECK (base_amount_minor IS NULL OR base_amount_minor <= 9007199254740991),
	FOREIGN KEY(transaction_id) REFERENCES transactions (id) ON DELETE SET NULL,
	FOREIGN KEY(source_record_id) REFERENCES source_records (id) ON DELETE SET NULL,
	FOREIGN KEY(duplicate_of_txn_id) REFERENCES transactions (id) ON DELETE SET NULL,
	FOREIGN KEY(suggested_category_id) REFERENCES categories (id) ON DELETE SET NULL,
	FOREIGN KEY(batch_id) REFERENCES import_batches (id) ON DELETE CASCADE,
	FOREIGN KEY(target_account_id) REFERENCES accounts (id) ON DELETE RESTRICT
);

CREATE TABLE login_sessions (
	id VARCHAR(64) NOT NULL,
	member_id VARCHAR(64) NOT NULL,
	token_hash VARCHAR(64) NOT NULL,
	created_at DATETIME NOT NULL,
	expires_at DATETIME NOT NULL,
	last_used_at DATETIME NOT NULL,
	revoked_at DATETIME,
	user_agent VARCHAR(300),
	PRIMARY KEY (id),
	FOREIGN KEY(member_id) REFERENCES members (id) ON DELETE CASCADE,
	UNIQUE (token_hash)
);

CREATE TABLE members (
	id VARCHAR(64) NOT NULL,
	identity VARCHAR(16) NOT NULL,
	name VARCHAR(80) NOT NULL,
	username VARCHAR(80) NOT NULL,
	password_hash TEXT NOT NULL,
	is_admin BOOLEAN NOT NULL,
	color VARCHAR(64) NOT NULL,
	active BOOLEAN NOT NULL,
	version INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_members_identity CHECK (identity IN ('dad','mom','son')),
	UNIQUE (identity),
	UNIQUE (username)
);

CREATE TABLE passkeys (
	id VARCHAR(64) NOT NULL,
	member_id VARCHAR(64) NOT NULL,
	name VARCHAR(100) NOT NULL,
	credential_id TEXT NOT NULL,
	public_key TEXT NOT NULL,
	sign_count BIGINT NOT NULL,
	transports JSON NOT NULL,
	device_type VARCHAR(32),
	backed_up BOOLEAN NOT NULL,
	created_at DATETIME NOT NULL,
	last_used_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(member_id) REFERENCES members (id) ON DELETE CASCADE,
	UNIQUE (credential_id)
);

CREATE TABLE period_locks (
	id VARCHAR(64) NOT NULL,
	starts_on DATE NOT NULL,
	ends_on DATE NOT NULL,
	locked_by VARCHAR(64) NOT NULL,
	reason VARCHAR(300) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_period_lock_range CHECK (starts_on <= ends_on),
	FOREIGN KEY(locked_by) REFERENCES members (id) ON DELETE RESTRICT
);

CREATE TABLE source_files (
	id VARCHAR(64) NOT NULL,
	batch_id VARCHAR(64) NOT NULL,
	filename VARCHAR(300) NOT NULL,
	sha256 VARCHAR(64) NOT NULL,
	mime_type VARCHAR(120) NOT NULL,
	size_bytes BIGINT NOT NULL,
	encrypted_path TEXT,
	expires_at DATETIME,
	deleted_at DATETIME,
	duplicate_of_file_id VARCHAR(64),
	parse_status VARCHAR(24) NOT NULL,
	parse_message VARCHAR(500) NOT NULL,
	record_count INTEGER NOT NULL,
	candidate_count INTEGER NOT NULL,
	rejected_count INTEGER NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_source_files_parse_status CHECK (parse_status IN ('processing','parsed','duplicate','needs_ocr','rejected','failed')),
	CONSTRAINT ck_source_files_counts CHECK (record_count >= 0 AND candidate_count >= 0 AND rejected_count >= 0),
	FOREIGN KEY(batch_id) REFERENCES import_batches (id) ON DELETE CASCADE,
	FOREIGN KEY(duplicate_of_file_id) REFERENCES source_files (id) ON DELETE SET NULL
);

CREATE TABLE source_records (
	id VARCHAR(64) NOT NULL,
	file_id VARCHAR(64) NOT NULL,
	source_ref VARCHAR(300),
	source_hash VARCHAR(64) NOT NULL,
	raw_payload JSON NOT NULL,
	linked_transaction_id VARCHAR(64),
	PRIMARY KEY (id),
	FOREIGN KEY(file_id) REFERENCES source_files (id) ON DELETE CASCADE,
	FOREIGN KEY(linked_transaction_id) REFERENCES transactions (id) ON DELETE SET NULL
);

CREATE TABLE "split_shares" (
	id VARCHAR(64) NOT NULL,
	split_id VARCHAR(64) NOT NULL,
	member_id VARCHAR(64) NOT NULL,
	share_minor BIGINT NOT NULL,
	status VARCHAR(16) NOT NULL,
	note TEXT,
	accepted_version INTEGER,
	responded_at DATETIME,
	PRIMARY KEY (id),
	CONSTRAINT uq_split_share_member UNIQUE (split_id, member_id),
	CONSTRAINT ck_split_shares_amount CHECK (share_minor > 0),
	CONSTRAINT ck_split_shares_status CHECK (status IN ('pending','accepted','returned')),
	CONSTRAINT ck_split_shares_amount_safe CHECK (share_minor <= 9007199254740991),
	FOREIGN KEY(split_id) REFERENCES splits (id) ON DELETE CASCADE,
	FOREIGN KEY(member_id) REFERENCES members (id) ON DELETE RESTRICT
);

CREATE TABLE splits (
	id VARCHAR(64) NOT NULL,
	transaction_id VARCHAR(64) NOT NULL,
	created_by VARCHAR(64) NOT NULL,
	status VARCHAR(16) NOT NULL,
	version INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_splits_status CHECK (status IN ('open','settled','returned')),
	FOREIGN KEY(created_by) REFERENCES members (id) ON DELETE RESTRICT,
	FOREIGN KEY(transaction_id) REFERENCES transactions (id) ON DELETE RESTRICT
);

CREATE TABLE "transactions" (
	id VARCHAR(64) NOT NULL,
	occurred_at DATETIME NOT NULL,
	type VARCHAR(16) NOT NULL,
	payee VARCHAR(300) NOT NULL,
	note TEXT NOT NULL,
	category_id VARCHAR(64),
	currency VARCHAR(3) NOT NULL,
	amount_minor BIGINT NOT NULL,
	base_currency VARCHAR(3) NOT NULL,
	base_amount_minor BIGINT NOT NULL,
	fx_rate NUMERIC(20, 10),
	source VARCHAR(16) NOT NULL,
	batch_id VARCHAR(64),
	refund_of_id VARCHAR(64),
	created_by VARCHAR(64) NOT NULL,
	version INTEGER NOT NULL,
	deleted_at DATETIME,
	deleted_by VARCHAR(64),
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_transactions_base_amount CHECK (base_amount_minor > 0),
	CONSTRAINT ck_transactions_source CHECK (source IN ('manual','import')),
	CONSTRAINT ck_transactions_amount CHECK (amount_minor > 0),
	CONSTRAINT ck_transactions_type CHECK (type IN ('expense','income','transfer','refund')),
	CONSTRAINT ck_transactions_amount_safe CHECK (amount_minor <= 9007199254740991),
	CONSTRAINT ck_transactions_base_amount_safe CHECK (base_amount_minor <= 9007199254740991),
	FOREIGN KEY(created_by) REFERENCES members (id) ON DELETE RESTRICT,
	FOREIGN KEY(batch_id) REFERENCES import_batches (id) ON DELETE SET NULL,
	FOREIGN KEY(category_id) REFERENCES categories (id) ON DELETE RESTRICT,
	FOREIGN KEY(refund_of_id) REFERENCES transactions (id) ON DELETE RESTRICT,
	FOREIGN KEY(deleted_by) REFERENCES members (id) ON DELETE RESTRICT
);

CREATE TABLE webauthn_challenges (
	id VARCHAR(64) NOT NULL,
	kind VARCHAR(16) NOT NULL,
	member_id VARCHAR(64) NOT NULL,
	challenge TEXT NOT NULL,
	expires_at DATETIME NOT NULL,
	used_at DATETIME,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_webauthn_kind CHECK (kind IN ('registration','authentication')),
	FOREIGN KEY(member_id) REFERENCES members (id) ON DELETE CASCADE
);

CREATE INDEX ix_accounts_owner_id ON accounts (owner_id);

CREATE INDEX ix_audit_events_actor_id ON audit_events (actor_id);

CREATE INDEX ix_audit_events_created_at ON audit_events (created_at);

CREATE INDEX ix_audit_events_entity_id ON audit_events (entity_id);

CREATE INDEX ix_entry_lines_account_id ON entry_lines (account_id);

CREATE INDEX ix_entry_lines_ledger_code ON entry_lines (ledger_code);

CREATE INDEX ix_entry_lines_transaction_id ON entry_lines (transaction_id);

CREATE INDEX ix_import_batches_job_id ON import_batches (job_id);

CREATE INDEX ix_import_batches_owner_id ON import_batches (owner_id);

CREATE INDEX ix_import_batches_target_account_id ON import_batches (target_account_id);

CREATE INDEX ix_import_candidates_batch_id ON import_candidates (batch_id);

CREATE INDEX ix_import_candidates_source_record_id ON import_candidates (source_record_id);

CREATE INDEX ix_import_candidates_target_account_id ON import_candidates (target_account_id);

CREATE INDEX ix_import_candidates_transaction_id ON import_candidates (transaction_id);

CREATE INDEX ix_login_sessions_member_id ON login_sessions (member_id);

CREATE INDEX ix_passkeys_member_id ON passkeys (member_id);

CREATE INDEX ix_source_files_batch_id ON source_files (batch_id);

CREATE INDEX ix_source_files_duplicate_of_file_id ON source_files (duplicate_of_file_id);

CREATE INDEX ix_source_files_sha256 ON source_files (sha256);

CREATE INDEX ix_source_records_file_id ON source_records (file_id);

CREATE INDEX ix_source_records_linked_transaction_id ON source_records (linked_transaction_id);

CREATE INDEX ix_source_records_source_hash ON source_records (source_hash);

CREATE INDEX ix_split_shares_member_id ON split_shares (member_id);

CREATE INDEX ix_split_shares_split_id ON split_shares (split_id);

CREATE INDEX ix_splits_created_by ON splits (created_by);

CREATE UNIQUE INDEX ix_splits_transaction_id ON splits (transaction_id);

CREATE INDEX ix_transactions_active_date ON transactions (deleted_at, occurred_at);

CREATE INDEX ix_transactions_created_by ON transactions (created_by);

CREATE INDEX ix_transactions_deleted_at ON transactions (deleted_at);

CREATE INDEX ix_transactions_occurred_at ON transactions (occurred_at);

CREATE INDEX ix_webauthn_challenges_expires_at ON webauthn_challenges (expires_at);

CREATE INDEX ix_webauthn_challenges_member_id ON webauthn_challenges (member_id);

CREATE UNIQUE INDEX uq_categories_sibling_name ON categories (kind, COALESCE(parent_id, ''), name);
