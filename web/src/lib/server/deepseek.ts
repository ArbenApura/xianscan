// PORTED FROM xianslate (src/lib/server/deepseek.ts) — OpenAI SDK + QUEUE + RETRY + COST MATH.
// THE ONE ADDITION: A `createClient()` FACTORY SO TESTS CAN INJECT A FAKE LLM (xianslate COULDN'T
// UNIT-TEST ITS LLM PATHS — HEAVY TDD REQUIRES THE INJECTION POINT).
import type { TranslationUsage } from '$lib/types';
// IMPORTED ENVS ($env/...)
import { env } from '$env/dynamic/private';
// IMPORTED DEP-MODULES
import OpenAI from 'openai';
import PQueue from 'p-queue';

// -- TYPES -- //

// USD-PER-TOKEN PRICE TRIPLE FOR ONE MODEL (CACHE-MISS INPUT, CACHE-HIT INPUT, OUTPUT).
interface ModelPricing {
	inputMiss: number;
	inputHit: number;
	output: number;
}

// -- CONSTANTS -- //

const apiKey = env.DEEPSEEK_API_KEY ?? '';

const baseURL = env.DEEPSEEK_BASE_URL ?? 'https://api.deepseek.com';

export const MODEL = env.DEEPSEEK_MODEL ?? 'deepseek-v4-flash';

// USER-SELECTABLE TRANSLATION MODELS (THE GLOBAL MODEL PICKER). FLASH = FAST/ECONOMICAL, PRO =
// HIGHER-QUALITY/COSTLIER. THE IDS ARE ENV-OVERRIDABLE SO A DEPLOYMENT CAN REMAP THEM TO WHATEVER ITS
// PROVIDER CALLS FLASH/PRO WITHOUT A CODE CHANGE. THE SELECTED MODEL PARTICIPATES IN THE TRANSLATION
// CACHE KEY, SO SWITCHING MODELS NEVER COLLIDES WITH AN EXISTING CACHED TRANSLATION.
export const MODEL_FLASH = env.DEEPSEEK_MODEL_FLASH ?? 'deepseek-v4-flash';

export const MODEL_PRO = env.DEEPSEEK_MODEL_PRO ?? 'deepseek-v4-pro';

export const TRANSLATION_MODELS: { id: string; label: string; blurb: string }[] = [
	{ id: MODEL_FLASH, label: 'Flash', blurb: 'Fast & economical — great for everyday reading' },
	{ id: MODEL_PRO, label: 'Pro', blurb: 'Higher-quality prose — slower and costs more' },
];

const ALLOWED_MODELS = new Set(TRANSLATION_MODELS.map((m) => m.id));

// -- FUNCTIONS -- //

// VALIDATE A REQUESTED MODEL AGAINST THE ALLOWLIST, FALLING BACK TO THE DEFAULT — NEVER LETS AN ARBITRARY
// CLIENT-SUPPLIED STRING REACH THE API.
export function resolveModel(model?: string | null): string {
	return model && ALLOWED_MODELS.has(model) ? model : MODEL;
}

// THE INJECTION POINT: TRANSLATE MODULES RECEIVE A CLIENT VIA OPTIONS; TESTS PASS A FAKE. THE REAL
// SINGLETON BELOW IS WHAT PRODUCTION USES.
export function createClient(key: string = apiKey, base: string = baseURL): OpenAI {
	return new OpenAI({ apiKey: key, baseURL: base });
}

// DEEPSEEK IS OPENAI-COMPATIBLE — POINT THE SDK AT ITS BASE URL
export const deepseek = createClient();

// deepseek-v4-* IS A HYBRID REASONING MODEL: BY DEFAULT IT EMITS `reasoning_content` AND SPENDS
// COMPLETION TOKENS "THINKING" BEFORE ANY REAL `content`. THAT IS ACTIVELY HARMFUL TO THIS APP:
//  - IT STALLS THE TRANSLATION STREAM (THE READER SEES NOTHING WHILE THE MODEL THINKS — LOOKS "STUCK").
//  - REASONING TOKENS ARE BILLED AS OUTPUT, SILENTLY INFLATING COST.
//  - WITH A BOUNDED max_tokens (TERM EXTRACTION) THE THINKING CAN EAT THE WHOLE BUDGET AND RETURN AN
//    EMPTY BODY — OBSERVED: EXTRACTION RETURNING {"terms":[]} FOR A PASSAGE FULL OF PROPER NOUNS.
// WE TRANSLATE DETERMINISTICALLY AND DON'T USE THE CHAIN-OF-THOUGHT, SO DISABLE THINKING BY
// DEFAULT. SET DEEPSEEK_REASONING=enabled TO OPT BACK IN (e.g. TO A/B HIGHER-EFFORT TRANSLATION).
const REASONING_ON = (env.DEEPSEEK_REASONING ?? 'disabled').toLowerCase() === 'enabled';

// PROCESS-WIDE CONCURRENCY CAP ON OUTBOUND DEEPSEEK CALLS — THE APP'S GLOBAL THROTTLE (SINGLE INSTANCE).
// WITHOUT THIS, PARALLEL PAGE JOBS FAN OUT UNTHROTTLED AND CAN TRIP RATE LIMITS / SPIKE COST.
// TUNE VIA DEEPSEEK_CONCURRENCY.
const CONCURRENCY = Math.max(1, Number(env.DEEPSEEK_CONCURRENCY ?? '64') || 64);

const queue = new PQueue({ concurrency: CONCURRENCY });

// DEEPSEEK PRICING (USD PER 1M TOKENS) BY MODEL — FROM api-docs.deepseek.com/quick_start/pricing (2026-06).
// A CACHE HIT IS ~50x CHEAPER THAN A MISS. EVERY FIELD IS ENV-OVERRIDABLE SO A DEPLOYMENT CAN TRACK PRICE
// CHANGES — OR REMAP FLASH/PRO — WITHOUT A CODE CHANGE. KEYED BY THE SAME MODEL_FLASH/MODEL_PRO IDS USED
// EVERYWHERE ELSE, SO REMAPPING A MODEL ID AUTOMATICALLY REMAPS ITS PRICE TOO.
const PRICING: Record<string, ModelPricing> = {
	[MODEL_FLASH]: {
		inputMiss: Number(env.DEEPSEEK_PRICE_FLASH_INPUT ?? '0.14') / 1_000_000,
		inputHit: Number(env.DEEPSEEK_PRICE_FLASH_CACHED ?? '0.0028') / 1_000_000,
		output: Number(env.DEEPSEEK_PRICE_FLASH_OUTPUT ?? '0.28') / 1_000_000,
	},
	[MODEL_PRO]: {
		inputMiss: Number(env.DEEPSEEK_PRICE_PRO_INPUT ?? '0.435') / 1_000_000,
		inputHit: Number(env.DEEPSEEK_PRICE_PRO_CACHED ?? '0.003625') / 1_000_000,
		output: Number(env.DEEPSEEK_PRICE_PRO_OUTPUT ?? '0.87') / 1_000_000,
	},
};

export function hasApiKey(): boolean {
	return apiKey.length > 0;
}

// EXTRA REQUEST-BODY FIELDS APPENDED TO EVERY DEEPSEEK CHAT CALL. SPREAD THIS INTO THE create()
// PARAMS LITERAL (NOT AS A PLAIN OBJECT) SO THE `thinking` KEY RIDES ALONG TO THE API WITHOUT
// TRIPPING TypeScript'S EXCESS-PROPERTY CHECK AND WITHOUT DISTURBING THE STREAM/NON-STREAM OVERLOAD.
export function thinkingParam(): Record<string, unknown> {
	return REASONING_ON ? {} : { thinking: { type: 'disabled' } };
}

// RUN AN LLM CALL THROUGH THE SHARED CONCURRENCY QUEUE (HOLDS A SLOT FOR THE FULL DURATION OF fn).
export function queued<T>(fn: () => Promise<T>): Promise<T> {
	return queue.add(fn, { throwOnTimeout: true }) as Promise<T>;
}

// TRANSIENT FAILURES (RATE LIMITS, 5xx, CONNECTION RESETS, TIMEOUTS) SHOULD NOT KILL A WHOLE CHAPTER
// TRANSLATION — RETRY THEM WITH EXPONENTIAL BACKOFF. NON-RETRYABLE ERRORS (e.g. 400/401) THROW AT ONCE.
function isRetryable(e: unknown): boolean {
	// A DELIBERATE ABORT (A force RE-RUN SUPERSEDING A JOB, OR THE CALLER CANCELLING) IS NEVER RETRYABLE —
	// THE SIGNAL IS STILL ABORTED, SO A RETRY WOULD ONLY THROW AGAIN AFTER A POINTLESS BACKOFF SLEEP (~9s).
	const name = (e as { name?: string })?.name;
	if (name === 'APIUserAbortError' || name === 'AbortError') return false;
	const status = (e as { status?: number })?.status;
	if (typeof status === 'number') return status === 408 || status === 409 || status === 429 || status >= 500;
	// NO HTTP STATUS → NETWORK/CONNECTION ERROR → RETRY
	return true;
}

export async function withRetry<T>(fn: () => Promise<T>, retries = 4): Promise<T> {
	let lastErr: unknown;
	for (let attempt = 0; attempt <= retries; attempt++) {
		try {
			return await fn();
		} catch (e) {
			lastErr = e;
			if (attempt === retries || !isRetryable(e)) break;
			const delay = Math.min(10_000, 600 * 2 ** attempt) + Math.floor(Math.random() * 400);
			await new Promise((r) => setTimeout(r, delay));
		}
	}
	throw lastErr;
}

// RESOLVE THE PER-MODEL PRICE TABLE, FALLING BACK TO FLASH FOR ANY UNKNOWN / ALIASED ID SO COST IS NEVER
// ZERO (e.g. A LEGACY USAGE ROW WHOSE model NO LONGER MATCHES A CONFIGURED ID).
function pricingFor(model: string): ModelPricing {
	return PRICING[model] ?? PRICING[MODEL_FLASH];
}

// COMPUTE APPROXIMATE COST FROM A USAGE OBJECT (HANDLES DEEPSEEK CACHE FIELDS). `model` SELECTS THE PRICE
// TABLE *AND* LABELS THE RESULTING USAGE ROW, SO FLASH AND PRO SPEND ARE COSTED AT THEIR OWN RATES.
export function computeUsage(
	usage: OpenAI.Completions.CompletionUsage | undefined,
	model: string = MODEL,
): TranslationUsage {
	const promptTokens = usage?.prompt_tokens ?? 0;
	const completionTokens = usage?.completion_tokens ?? 0;
	// DEEPSEEK REPORTS prompt_cache_hit_tokens / prompt_cache_miss_tokens
	const u = usage as unknown as {
		prompt_cache_hit_tokens?: number;
		prompt_cache_miss_tokens?: number;
		prompt_tokens_details?: { cached_tokens?: number };
	};
	const cachedTokens = u?.prompt_cache_hit_tokens ?? u?.prompt_tokens_details?.cached_tokens ?? 0;
	const missTokens = u?.prompt_cache_miss_tokens ?? Math.max(0, promptTokens - cachedTokens);
	const price = pricingFor(model);
	const costUsd = missTokens * price.inputMiss + cachedTokens * price.inputHit + completionTokens * price.output;
	return { model, promptTokens, cachedTokens, completionTokens, costUsd };
}
