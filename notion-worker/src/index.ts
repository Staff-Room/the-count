import { Worker } from "@notionhq/workers";
import * as Builder from "@notionhq/workers/builder";
import * as Schema from "@notionhq/workers/schema";

const worker = new Worker();
export default worker;

/* ——— Plaid client (REST) ——— */

type PlaidItemConfig = {
	item_id: string;
	access_token: string;
	institution_name?: string;
};

type TxSyncState = {
	cursors: Record<string, string>;
	itemIndex: number;
	pendingPlaidCursor?: string;
};

function plaidBaseUrl(): string {
	const env = (process.env.PLAID_ENV ?? process.env.PLAID_ENVIRONMENT ?? "sandbox").toLowerCase();
	if (env === "sandbox") return "https://sandbox.plaid.com";
	if (env === "development") return "https://development.plaid.com";
	return "https://production.plaid.com";
}

function getPlaidItems(): PlaidItemConfig[] {
	const raw = process.env.PLAID_ITEMS_JSON ?? process.env.PLAID_ITEMS ?? "[]";
	try {
		const parsed = JSON.parse(raw) as PlaidItemConfig[];
		if (!Array.isArray(parsed)) return [];
		return parsed.filter((x) => x?.item_id && x?.access_token);
	} catch {
		return [];
	}
}

function requirePlaidCredentials(): { client_id: string; secret: string } {
	const client_id = process.env.PLAID_CLIENT_ID ?? "";
	const secret = process.env.PLAID_SECRET ?? "";
	if (!client_id || !secret) {
		throw new Error(
			"PLAID_CLIENT_ID and PLAID_SECRET must be set (use ntn workers env set ...).",
		);
	}
	return { client_id, secret };
}

async function plaidPost<T>(path: string, body: Record<string, unknown>): Promise<T> {
	const cred = requirePlaidCredentials();
	const url = `${plaidBaseUrl()}${path}`;
	const res = await fetch(url, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({
			...body,
			client_id: cred.client_id,
			secret: cred.secret,
		}),
	});
	const json = (await res.json()) as T & { error_message?: string; error_code?: string };
	if (!res.ok) {
		const msg =
			(json as { error_message?: string }).error_message ??
			JSON.stringify(json).slice(0, 500);
		throw new Error(`Plaid ${path} failed (${res.status}): ${msg}`);
	}
	return json;
}

/* ——— Notion databases ——— */

const plaidPacer = worker.pacer("plaidApi", {
	allowedRequests: 8,
	intervalMs: 1000,
});

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

type PlaidAccount = {
	account_id: string;
	name?: string;
	official_name?: string;
	type?: string;
	subtype?: string;
	mask?: string;
	balances?: {
		available?: number | null;
		current?: number | null;
		limit?: number | null;
		iso_currency_code?: string | null;
	};
};

worker.sync("plaidAccountsSync", {
	database: bankAccounts,
	mode: "replace",
	schedule: "1h",
	execute: async () => {
		const items = getPlaidItems();
		if (items.length === 0) {
			return { changes: [], hasMore: false };
		}

		const changes: Array<{
			type: "upsert";
			key: string;
			properties: {
				"Account ID": ReturnType<typeof Builder.richText>;
				Name: ReturnType<typeof Builder.title>;
				"Item ID": ReturnType<typeof Builder.richText>;
				"Institution name": ReturnType<typeof Builder.richText>;
				Mask: ReturnType<typeof Builder.richText>;
				"Official name": ReturnType<typeof Builder.richText>;
				Type: ReturnType<typeof Builder.richText>;
				Subtype: ReturnType<typeof Builder.richText>;
				"ISO currency": ReturnType<typeof Builder.richText>;
				"Current balance": ReturnType<typeof Builder.number>;
				"Available balance": ReturnType<typeof Builder.number>;
				"Credit limit": ReturnType<typeof Builder.number>;
			};
		}> = [];

		for (const item of items) {
			await plaidPacer.wait();
			const data = await plaidPost<{ accounts: PlaidAccount[] }>("/accounts/get", {
				access_token: item.access_token,
			});
			const inst = item.institution_name ?? "";

			for (const a of data.accounts ?? []) {
				const id = a.account_id;
				if (!id) continue;
				const b = a.balances ?? {};
				changes.push({
					type: "upsert",
					key: id,
					properties: {
						"Account ID": Builder.richText(id),
						Name: Builder.title(sOrEmpty(a.name) || id),
						"Item ID": Builder.richText(item.item_id),
						"Institution name": Builder.richText(inst),
						Mask: Builder.richText(sOrEmpty(a.mask)),
						"Official name": Builder.richText(sOrEmpty(a.official_name)),
						Type: Builder.richText(sOrEmpty(a.type)),
						Subtype: Builder.richText(sOrEmpty(a.subtype)),
						"ISO currency": Builder.richText(sOrEmpty(b.iso_currency_code)),
						"Current balance": Builder.number(nOr0(b.current)),
						"Available balance": Builder.number(nOr0(b.available)),
						"Credit limit": Builder.number(nOr0(b.limit)),
					},
				});
			}
		}

		return { changes, hasMore: false };
	},
});

type PlaidTx = {
	transaction_id: string;
	account_id: string;
	amount: number;
	iso_currency_code?: string | null;
	date?: string | null;
	authorized_date?: string | null;
	name?: string | null;
	merchant_name?: string | null;
	pending?: boolean | null;
	personal_finance_category?: {
		primary?: string | null;
		detailed?: string | null;
	} | null;
	payment_channel?: string | null;
};

type TransactionsSyncResponse = {
	added: PlaidTx[];
	modified: PlaidTx[];
	removed: { transaction_id: string }[];
	has_more: boolean;
	next_cursor: string;
};

worker.sync("plaidTransactionsSync", {
	database: bankTransactions,
	mode: "incremental",
	schedule: "5m",
	execute: async (state: TxSyncState | undefined) => {
		const items = getPlaidItems();
		if (items.length === 0) {
			return {
				changes: [],
				hasMore: false,
				nextState: { cursors: {}, itemIndex: 0 },
			};
		}

		const st: TxSyncState = state ?? {
			cursors: {},
			itemIndex: 0,
		};

		if (st.itemIndex >= items.length) {
			return {
				changes: [],
				hasMore: false,
				nextState: { cursors: st.cursors, itemIndex: 0 },
			};
		}

		const item = items[st.itemIndex]!;
		await plaidPacer.wait();

		const body: Record<string, unknown> = {
			access_token: item.access_token,
			options: { include_personal_finance_category: true },
		};

		const cursorForRequest =
			st.pendingPlaidCursor ?? st.cursors[item.item_id] ?? null;
		if (cursorForRequest) {
			body.cursor = cursorForRequest;
		}

		const resp = await plaidPost<TransactionsSyncResponse>(
			"/transactions/sync",
			body,
		);

		const changes: Array<
			| {
					type: "upsert";
					key: string;
					properties: {
						"Transaction ID": ReturnType<typeof Builder.richText>;
						Name: ReturnType<typeof Builder.title>;
						Account: ReturnType<typeof Builder.relation>[];
						Amount: ReturnType<typeof Builder.number>;
						Date: ReturnType<typeof Builder.date>;
						"Authorized date": ReturnType<typeof Builder.date>;
						Pending: ReturnType<typeof Builder.checkbox>;
						"Merchant name": ReturnType<typeof Builder.richText>;
						"Primary category": ReturnType<typeof Builder.richText>;
						"Detailed category": ReturnType<typeof Builder.richText>;
						"Payment channel": ReturnType<typeof Builder.richText>;
						"ISO currency": ReturnType<typeof Builder.richText>;
						"Item ID": ReturnType<typeof Builder.richText>;
					};
			  }
			| { type: "delete"; key: string }
		> = [];

		const mapTx = (tx: PlaidTx) => {
			const pfc = tx.personal_finance_category ?? undefined;
			const auth = tx.authorized_date
				? dateOrEpoch(tx.authorized_date)
				: dateOrEpoch(tx.date);
			changes.push({
				type: "upsert",
				key: tx.transaction_id,
				properties: {
					"Transaction ID": Builder.richText(tx.transaction_id),
					Name: Builder.title(sOrEmpty(tx.name) || "Transaction"),
					Account: [Builder.relation(tx.account_id)],
					Amount: Builder.number(Number(tx.amount)),
					Date: Builder.date(dateOrEpoch(tx.date)),
					"Authorized date": Builder.date(auth),
					Pending: Builder.checkbox(Boolean(tx.pending)),
					"Merchant name": Builder.richText(sOrEmpty(tx.merchant_name)),
					"Primary category": Builder.richText(sOrEmpty(pfc?.primary)),
					"Detailed category": Builder.richText(sOrEmpty(pfc?.detailed)),
					"Payment channel": Builder.richText(sOrEmpty(tx.payment_channel)),
					"ISO currency": Builder.richText(sOrEmpty(tx.iso_currency_code)),
					"Item ID": Builder.richText(item.item_id),
				},
			});
		};

		for (const tx of resp.added ?? []) mapTx(tx);
		for (const tx of resp.modified ?? []) mapTx(tx);
		for (const r of resp.removed ?? []) {
			if (r.transaction_id) {
				changes.push({ type: "delete", key: r.transaction_id });
			}
		}

		if (resp.has_more) {
			return {
				changes,
				hasMore: true,
				nextState: {
					cursors: st.cursors,
					itemIndex: st.itemIndex,
					pendingPlaidCursor: resp.next_cursor,
				},
			};
		}

		const newCursors = { ...st.cursors, [item.item_id]: resp.next_cursor };
		const nextItemIndex = st.itemIndex + 1;

		if (nextItemIndex >= items.length) {
			return {
				changes,
				hasMore: false,
				nextState: {
					cursors: newCursors,
					itemIndex: 0,
				},
			};
		}

		return {
			changes,
			hasMore: true,
			nextState: {
				cursors: newCursors,
				itemIndex: nextItemIndex,
			},
		};
	},
});
