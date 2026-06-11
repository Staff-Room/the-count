import { Worker } from "@notionhq/workers";
import * as Builder from "@notionhq/workers/builder";
import * as Schema from "@notionhq/workers/schema";

const worker = new Worker();
export default worker;

/* ——— Supabase (PostgREST) client ———
 *
 * The worker no longer calls Plaid. the-count's sync owns the Plaid pull and
 * writes to Supabase; this worker mirrors plaid_accounts and the
 * plaid_transactions_coded view (transactions + resolved Schedule C line +
 * custom category) into Notion. Required env: SUPABASE_URL,
 * SUPABASE_SERVICE_ROLE_KEY, PLAID_ENV (sandbox | production).
 */

function plaidEnv(): string {
	let env = (process.env.PLAID_ENV ?? process.env.PLAID_ENVIRONMENT ?? "sandbox").toLowerCase();
	if (env === "development") env = "production";
	return env;
}

function supabaseConfig(): { url: string; key: string } {
	const url = (process.env.SUPABASE_URL ?? "").replace(/\/+$/, "");
	const key = process.env.SUPABASE_SERVICE_ROLE_KEY ?? "";
	if (!url || !key) {
		throw new Error(
			"SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set (use ntn workers env set ...).",
		);
	}
	return { url, key };
}

async function supabaseGet<T>(path: string, params: Record<string, string>): Promise<T> {
	const { url, key } = supabaseConfig();
	const qs = new URLSearchParams(params).toString();
	const res = await fetch(`${url}/rest/v1/${path}?${qs}`, {
		headers: { apikey: key, Authorization: `Bearer ${key}` },
	});
	const json = (await res.json()) as T;
	if (!res.ok) {
		throw new Error(
			`Supabase ${path} failed (${res.status}): ${JSON.stringify(json).slice(0, 500)}`,
		);
	}
	return json;
}

const supabasePacer = worker.pacer("supabaseApi", {
	allowedRequests: 10,
	intervalMs: 1000,
});

/* ——— Notion databases ——— */

const SCHEDULE_C_OPTIONS = [
	"Line 1",
	"Line 6",
	"Line 8",
	"Line 9",
	"Line 10",
	"Line 11",
	"Line 12",
	"Line 13",
	"Line 14",
	"Line 15",
	"Line 16a",
	"Line 16b",
	"Line 17",
	"Line 18",
	"Line 19",
	"Line 20a",
	"Line 20b",
	"Line 21",
	"Line 22",
	"Line 23",
	"Line 24a",
	"Line 24b",
	"Line 25",
	"Line 26",
	"Line 27a",
	"Needs review",
] as const;

const bankAccounts = worker.database("bankAccounts", {
	type: "managed",
	initialTitle: "Bank accounts (Plaid)",
	primaryKeyProperty: "Account ID",
	schema: {
		properties: {
			"Account ID": Schema.richText(),
			Name: Schema.title(),
			"Item ID": Schema.richText(),
			"Institution name": Schema.richText(),
			Mask: Schema.richText(),
			"Official name": Schema.richText(),
			Type: Schema.richText(),
			Subtype: Schema.richText(),
			"ISO currency": Schema.richText(),
			"Current balance": Schema.number(),
			"Available balance": Schema.number(),
			"Credit limit": Schema.number(),
		},
	},
});

const bankTransactions = worker.database("bankTransactions", {
	type: "managed",
	initialTitle: "Bank transactions (Plaid)",
	primaryKeyProperty: "Transaction ID",
	schema: {
		properties: {
			"Transaction ID": Schema.richText(),
			Name: Schema.title(),
			Account: Schema.relation("bankAccounts", {
				twoWay: true,
				relatedPropertyName: "Transactions",
			}),
			Amount: Schema.number(),
			Date: Schema.date(),
			"Authorized date": Schema.date(),
			Pending: Schema.checkbox(),
			"Merchant name": Schema.richText(),
			"Primary category": Schema.richText(),
			"Detailed category": Schema.richText(),
			"Schedule C line": Schema.select(
				SCHEDULE_C_OPTIONS.map((name) => ({ name })),
			),
			"Custom category": Schema.richText(),
			"Payment channel": Schema.richText(),
			"ISO currency": Schema.richText(),
			"Item ID": Schema.richText(),
		},
	},
});

function nOr0(v: unknown): number {
	if (v === null || v === undefined) return 0;
	const x = Number(v);
	return Number.isFinite(x) ? x : 0;
}

function sOrEmpty(v: unknown): string {
	if (v === null || v === undefined) return "";
	return String(v);
}

function dateOrEpoch(d: unknown): string {
	if (d === null || d === undefined || d === "") return "1970-01-01";
	if (typeof d === "string") return d.slice(0, 10);
	return "1970-01-01";
}

/* ——— Accounts sync (replace, hourly) ——— */

type AccountRow = {
	account_id: string;
	item_id: string;
	name: string | null;
	official_name: string | null;
	mask: string | null;
	type: string | null;
	subtype: string | null;
	current_balance: number | null;
	available_balance: number | null;
	credit_limit: number | null;
	iso_currency_code: string | null;
};

type ItemRow = { item_id: string; institution_name: string | null };

worker.sync("plaidAccountsSync", {
	database: bankAccounts,
	mode: "replace",
	schedule: "1h",
	execute: async () => {
		await supabasePacer.wait();
		const accounts = await supabaseGet<AccountRow[]>("plaid_accounts", {
			select:
				"account_id,item_id,name,official_name,mask,type,subtype,current_balance,available_balance,credit_limit,iso_currency_code",
			env: `eq.${plaidEnv()}`,
			order: "account_id",
			limit: "1000",
		});

		await supabasePacer.wait();
		const items = await supabaseGet<ItemRow[]>("plaid_items", {
			select: "item_id,institution_name",
			env: `eq.${plaidEnv()}`,
		});
		const instByItem = new Map(items.map((i) => [i.item_id, i.institution_name ?? ""]));

		const changes = accounts.map((a) => ({
			type: "upsert" as const,
			key: a.account_id,
			properties: {
				"Account ID": Builder.richText(a.account_id),
				Name: Builder.title(sOrEmpty(a.name) || a.account_id),
				"Item ID": Builder.richText(a.item_id),
				"Institution name": Builder.richText(instByItem.get(a.item_id) ?? ""),
				Mask: Builder.richText(sOrEmpty(a.mask)),
				"Official name": Builder.richText(sOrEmpty(a.official_name)),
				Type: Builder.richText(sOrEmpty(a.type)),
				Subtype: Builder.richText(sOrEmpty(a.subtype)),
				"ISO currency": Builder.richText(sOrEmpty(a.iso_currency_code)),
				"Current balance": Builder.number(nOr0(a.current_balance)),
				"Available balance": Builder.number(nOr0(a.available_balance)),
				"Credit limit": Builder.number(nOr0(a.credit_limit)),
			},
		}));

		return { changes, hasMore: false };
	},
});

/* ——— Transactions: backfill (replace, manual) + delta (incremental, 5m) ——— */

type CodedTxRow = {
	transaction_id: string;
	account_id: string;
	item_id: string;
	amount: number;
	iso_currency_code: string | null;
	date: string | null;
	authorized_date: string | null;
	name: string | null;
	merchant_name: string | null;
	pending: boolean | null;
	primary_category: string | null;
	detailed_category: string | null;
	payment_channel: string | null;
	schedule_c_code: string | null;
	schedule_c_line: string | null;
	custom_category_name: string | null;
	updated_at: string;
};

const TX_SELECT =
	"transaction_id,account_id,item_id,amount,iso_currency_code,date,authorized_date," +
	"name,merchant_name,pending,primary_category,detailed_category,payment_channel," +
	"schedule_c_code,schedule_c_line,custom_category_name,updated_at";

const TX_PAGE = 100;

function scheduleCOption(row: CodedTxRow): string {
	if (!row.schedule_c_code || row.schedule_c_code === "L99") return "Needs review";
	return row.schedule_c_line ?? "Needs review";
}

function txUpsert(row: CodedTxRow) {
	const auth = row.authorized_date
		? dateOrEpoch(row.authorized_date)
		: dateOrEpoch(row.date);
	return {
		type: "upsert" as const,
		key: row.transaction_id,
		properties: {
			"Transaction ID": Builder.richText(row.transaction_id),
			Name: Builder.title(sOrEmpty(row.name) || "Transaction"),
			Account: [Builder.relation(row.account_id)],
			Amount: Builder.number(Number(row.amount)),
			Date: Builder.date(dateOrEpoch(row.date)),
			"Authorized date": Builder.date(auth),
			Pending: Builder.checkbox(Boolean(row.pending)),
			"Merchant name": Builder.richText(sOrEmpty(row.merchant_name)),
			"Primary category": Builder.richText(sOrEmpty(row.primary_category)),
			"Detailed category": Builder.richText(sOrEmpty(row.detailed_category)),
			"Schedule C line": Builder.select(scheduleCOption(row)),
			"Custom category": Builder.richText(sOrEmpty(row.custom_category_name)),
			"Payment channel": Builder.richText(sOrEmpty(row.payment_channel)),
			"ISO currency": Builder.richText(sOrEmpty(row.iso_currency_code)),
			"Item ID": Builder.richText(row.item_id),
		},
	};
}

// Backfill: full re-mirror; replace-mode mark-and-sweep also handles rows
// deleted from Supabase (the delta sync cannot see deletions).
// Re-run via: ntn workers sync state reset plaidTransactionsBackfill && ntn workers sync trigger plaidTransactionsBackfill
worker.sync("plaidTransactionsBackfill", {
	database: bankTransactions,
	mode: "replace",
	schedule: "manual",
	execute: async (state: { offset?: number } | undefined) => {
		const offset = state?.offset ?? 0;
		await supabasePacer.wait();
		const rows = await supabaseGet<CodedTxRow[]>("plaid_transactions_coded", {
			select: TX_SELECT,
			env: `eq.${plaidEnv()}`,
			order: "transaction_id",
			limit: String(TX_PAGE),
			offset: String(offset),
		});
		const hasMore = rows.length === TX_PAGE;
		return {
			changes: rows.map(txUpsert),
			hasMore,
			nextState: hasMore ? { offset: offset + TX_PAGE } : undefined,
		};
	},
});

// Delta: keyset pagination on (updated_at, transaction_id). the-count stamps
// whole sync batches with one updated_at, so a plain `gt` cursor could skip
// same-timestamp rows across page boundaries.
type TxSyncState = { since?: string; sinceId?: string };

worker.sync("plaidTransactionsSync", {
	database: bankTransactions,
	mode: "incremental",
	schedule: "5m",
	execute: async (state: TxSyncState | undefined) => {
		const params: Record<string, string> = {
			select: TX_SELECT,
			env: `eq.${plaidEnv()}`,
			order: "updated_at.asc,transaction_id.asc",
			limit: String(TX_PAGE),
		};
		if (state?.since && state?.sinceId) {
			params.or = `(updated_at.gt.${state.since},and(updated_at.eq.${state.since},transaction_id.gt.${state.sinceId}))`;
		} else if (state?.since) {
			params.updated_at = `gt.${state.since}`;
		}

		await supabasePacer.wait();
		const rows = await supabaseGet<CodedTxRow[]>("plaid_transactions_coded", params);

		if (rows.length === 0) {
			return { changes: [], hasMore: false, nextState: state };
		}

		const last = rows[rows.length - 1]!;
		return {
			changes: rows.map(txUpsert),
			hasMore: rows.length === TX_PAGE,
			nextState: { since: last.updated_at, sinceId: last.transaction_id },
		};
	},
});
