// TRANSLATE TESTS — PROMPT SHAPE, GLOSSARY ENFORCEMENT (THE BLOCK IS A SEPARATE SYSTEM MESSAGE),
// JSON SALVAGE PARSING, DEGENERATE-DETECTION REFILL, USAGE ACCRUAL. THE LLM IS A FAKE CLIENT —
// TRANSLATE IS THE FIRST MODULE IN THIS APP WHOSE LLM PATHS ARE UNIT-TESTED (xianslate COULDN'T).
import { describe, expect, it } from 'vitest';
import type OpenAI from 'openai';
import type { TermDraft } from '$lib/types';
import {
	buildMessages,
	glossaryBlock,
	looksDegenerate,
	parseTranslations,
	systemPrompt,
	translatePage,
	userPrompt,
} from '$lib/server/translate';

const PAIR = { sourceLang: 'zh-Hans', targetLang: 'en' };

function fakeClient(responses: Array<string | Error>, usage: unknown = { prompt_tokens: 100, completion_tokens: 20, total_tokens: 120 }) {
	let call = 0;
	const client = {
		chat: {
			completions: {
				create: async () => {
					const r = responses[Math.min(call, responses.length - 1)];
					call++;
					if (r instanceof Error) throw r;
					return { choices: [{ message: { content: r } }], usage };
				},
			},
		},
	} as unknown as OpenAI;
	return { client, callCount: () => call };
}

// -- PROMPT CONSTRUCTION -- //

describe('systemPrompt', () => {
	it('covers the manhua localization rules', () => {
		const p = systemPrompt('zh-Hans', 'en');
		expect(p).toMatch(/manhua/);
		expect(p).toMatch(/JSON object/);
		expect(p).toContain('zh-Hans');
	});
});

describe('glossaryBlock', () => {
	const term = (t: Partial<TermDraft> & { source: string; target: string }): TermDraft => ({
		gender: 'neuter',
		status: 'user',
		aliases: [],
		pinned: false,
		...t,
	});

	it('returns null for an empty glossary', () => {
		expect(glossaryBlock([], 'zh-Hans', 'en')).toBeNull();
	});

	it('formats pinned-first with aliases, gender and context', () => {
		const block = glossaryBlock(
			[
				term({ source: '主角', target: 'MC', gender: 'masculine', context: 'the protagonist' }),
				term({ source: '系统', target: 'System', pinned: true, aliases: ['系统君'] }),
			],
			'zh-Hans',
			'en',
		);
		expect(block).toContain('★系统 (also: 系统君) = System');
		expect(block).toContain('★主角 = MC [masculine] — the protagonist');
		// PINNED TERM COMES FIRST
		expect(block!.indexOf('★系统')).toBeLessThan(block!.indexOf('★主角'));
	});

	it('is injected as a separate system message between prompt and user content', () => {
		const regions = [{ id: 'r0', text: '你好', category: 'dialogue' as const }];
		const messages = buildMessages(regions, [term({ source: '系统', target: 'System' })], PAIR);
		expect(messages).toHaveLength(3);
		expect(messages[0].role).toBe('system');
		expect(messages[1]).toMatchObject({ role: 'system', content: expect.stringContaining('★系统 = System') });
		expect(messages[2].role).toBe('user');
		expect(String(messages[2].content)).toContain('r0');
	});
});

describe('userPrompt', () => {
	it('carries ids, text and category for every region', () => {
		const p = userPrompt([
			{ id: 'r0', text: '轰', category: 'sfx' },
			{ id: 'r1', text: '你好', category: 'dialogue' },
		]);
		expect(p).toContain('"id": "r0"');
		expect(p).toContain('"category": "sfx"');
		expect(p).toContain('"text": "轰"');
	});
});

// -- PARSING -- //

describe('parseTranslations', () => {
	const ids = new Set(['r0', 'r1', 'r2']);

	it('parses a clean JSON object', () => {
		const out = parseTranslations('{"r0": "Hi", "r1": "BOOM!", "r2": "System"}', ids);
		expect([...out!.entries()]).toEqual([
			['r0', 'Hi'],
			['r1', 'BOOM!'],
			['r2', 'System'],
		]);
	});

	it('strips markdown fences', () => {
		const out = parseTranslations('```json\n{"r0": "Hi"}\n```', ids);
		expect(out!.get('r0')).toBe('Hi');
	});

	it('salvages partial objects when the model adds commentary', () => {
		const out = parseTranslations('Here you go: {"r0": "Hi", "r1": "BOOM!"} hope that helps', ids);
		expect(out!.get('r0')).toBe('Hi');
		expect(out!.get('r1')).toBe('BOOM!');
	});

	it('ignores unknown ids and empty texts', () => {
		const out = parseTranslations('{"r0": "Hi", "rX": "sneaky", "r1": ""}', ids);
		expect([...out!.keys()]).toEqual(['r0']);
	});

	it('returns null when nothing parses', () => {
		expect(parseTranslations('I am sorry, I cannot do that', ids)).toBeNull();
		expect(parseTranslations('', ids)).toBeNull();
	});

	it('un-escapes embedded quotes', () => {
		const out = parseTranslations('{"r0": "He said \\"hi\\""}', ids);
		expect(out!.get('r0')).toBe('He said "hi"');
	});

	it('preserves \\n line breaks (multi-line bubble paragraphs)', () => {
		const out = parseTranslations('{"r0": "Hello there.\\nSecond line."}', ids);
		expect(out!.get('r0')).toBe('Hello there.\nSecond line.');
	});
});

describe('looksDegenerate', () => {
	it('flags empty and over-expanded translations', () => {
		expect(looksDegenerate('', '你好')).toBe(true);
		expect(looksDegenerate('This sentence is way too long', '你好')).toBe(true);
	});

	it('accepts sane translations', () => {
		expect(looksDegenerate('Hello', '你好')).toBe(false);
		expect(looksDegenerate('BOOM!', '轰')).toBe(false);
	});
});

// -- END-TO-END (FAKE LLM) -- //

describe('translatePage', () => {
	const regions = [
		{ id: 'r0', text: '你好', category: 'dialogue' as const },
		{ id: 'r1', text: '轰', category: 'sfx' as const },
	];

	it('returns a translation per region with accrued usage', async () => {
		const { client, callCount } = fakeClient(['{"r0": "Hello", "r1": "BOOM!"}']);
		const result = await translatePage(regions, [], PAIR, { client });
		expect([...result.byRegion.entries()]).toEqual([
			['r0', 'Hello'],
			['r1', 'BOOM!'],
		]);
		expect(result.usage.promptTokens).toBe(100);
		expect(result.usage.completionTokens).toBe(20);
		expect(result.usage.costUsd).toBeGreaterThan(0);
		expect(callCount()).toBe(1);
	});

	it('refills regions the first pass missed or mangled', async () => {
		const { client, callCount } = fakeClient([
			'{"r0": "Hello"}', // r1 MISSING
			'{"r1": "BOOM!"}',
		]);
		const result = await translatePage(regions, [], PAIR, { client });
		expect(result.byRegion.get('r0')).toBe('Hello');
		expect(result.byRegion.get('r1')).toBe('BOOM!');
		expect(callCount()).toBe(2);
		// BOTH CALLS' USAGE IS ACCRUED
		expect(result.usage.promptTokens).toBe(200);
	});

	it('leaves a region empty when the refill also fails', async () => {
		const { client } = fakeClient(['{"r0": "Hello"}', 'gibberish']);
		const result = await translatePage(regions, [], PAIR, { client });
		expect(result.byRegion.get('r0')).toBe('Hello');
		expect(result.byRegion.has('r1')).toBe(false);
	});

	it('drops degenerate (over-expanded) translations and refills them', async () => {
		const { client, callCount } = fakeClient([
			'{"r0": "This is an extremely long explanation", "r1": "BOOM!"}', // r0 DEGENERATE
			'{"r0": "Hi"}',
		]);
		const result = await translatePage(regions, [], PAIR, { client });
		expect(result.byRegion.get('r0')).toBe('Hi');
		expect(callCount()).toBe(2);
	});

	it('empty region list short-circuits without calling the LLM', async () => {
		const { client, callCount } = fakeClient(['nope']);
		const result = await translatePage([], [], PAIR, { client });
		expect(result.byRegion.size).toBe(0);
		expect(callCount()).toBe(0);
	});

	it('the fake client receives the model allowlisted', async () => {
		let seenModel = '';
		const client = {
			chat: {
				completions: {
					create: async (params: { model: string }) => {
						seenModel = params.model;
						return { choices: [{ message: { content: '{"r0": "Hi"}' } }], usage: undefined };
					},
				},
			},
		} as unknown as OpenAI;
		await translatePage(regions, [], PAIR, { client, model: 'gpt-4' }); // NOT ALLOWLISTED
		expect(seenModel).not.toBe('gpt-4'); // resolveModel FALLS BACK TO THE DEFAULT
	});
});
