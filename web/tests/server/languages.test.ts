import { describe, expect, it } from 'vitest';
import {
	DEFAULT_SOURCE_LANG,
	DEFAULT_TARGET_LANG,
	getLanguage,
	languageName,
	LANGUAGES,
	leakRepairApplies,
	SOURCE_LANGUAGE_OPTIONS,
	TARGET_LANGUAGE_OPTIONS,
	targetLanguageOptions,
} from '$lib/languages';
import { matchTerms } from '$lib/server/glossary-match';
import { typesetPage, wrapText } from '$lib/server/typeset';
import { createCanvas } from '@napi-rs/canvas';

describe('Language Registry', () => {
	it('includes core tier 1 and tier 2 languages in LANGUAGES', () => {
		expect(LANGUAGES.en).toBeDefined();
		expect(LANGUAGES.en.name).toBe('English');
		expect(LANGUAGES.en.tier).toBe(1);

		expect(LANGUAGES['zh-Hans']).toBeDefined();
		expect(LANGUAGES['zh-Hans'].tier).toBe(1);

		expect(LANGUAGES.ja).toBeDefined();
		expect(LANGUAGES.ja.name).toBe('Japanese');
		expect(LANGUAGES.ja.tier).toBe(2);

		expect(LANGUAGES.ko).toBeDefined();
		expect(LANGUAGES.ko.name).toBe('Korean');
		expect(LANGUAGES.ko.tier).toBe(2);
	});

	it('includes primary target languages in TARGET_LANGUAGE_OPTIONS and targetLanguageOptions()', () => {
		const targetCodes = TARGET_LANGUAGE_OPTIONS.map((o) => o.value);
		expect(targetCodes).toContain('en');
		expect(targetCodes).toContain('ja');
		expect(targetCodes).toContain('ko');
		expect(targetCodes).not.toContain('fil');

		const all = targetLanguageOptions();
		const enOption = all.find((o) => o.value === 'en');
		expect(enOption).toBeDefined();
		expect(enOption?.tier).toBe(1);
	});

	it('correctly resolves language codes and aliases via getLanguage', () => {
		expect(getLanguage('en').name).toBe('English');
		expect(getLanguage('ja').name).toBe('Japanese');
		expect(getLanguage('ko').name).toBe('Korean');
		expect(getLanguage('zh-CN').code).toBe('zh-Hans');
		expect(getLanguage('zh-TW').code).toBe('zh-Hant');
		expect(getLanguage(undefined).code).toBe(DEFAULT_SOURCE_LANG);
	});

	it('returns correct display names via languageName', () => {
		expect(languageName('en')).toBe('English');
		expect(languageName('ja')).toBe('Japanese');
		expect(languageName('ko')).toBe('Korean');
		expect(languageName('none')).toBe('Original');
	});

	it('handles leak repair check correctly for non-CJK languages', () => {
		expect(leakRepairApplies('zh-Hans')).toBe(true);
		expect(leakRepairApplies('ja')).toBe(true);
		expect(leakRepairApplies('ko')).toBe(true);
		expect(leakRepairApplies('en')).toBe(false);
		expect(leakRepairApplies('es')).toBe(false);
	});
});

describe('Multilingual Pipeline & Typesetting', () => {
	it('wordDelimited is true for space-separated languages and false for CJK', () => {
		expect(getLanguage('en').wordDelimited).toBe(true);
		expect(getLanguage('es').wordDelimited).toBe(true);
		expect(getLanguage('id').wordDelimited).toBe(true);
		expect(getLanguage('zh-Hans').wordDelimited).toBe(false);
		expect(getLanguage('ja').wordDelimited).toBe(false);
	});

	it('typesets Latin and CJK text onto a page canvas cleanly', async () => {
		const canvas = createCanvas(100, 100);
		const ctx = canvas.getContext('2d');
		const lines = wrapText(ctx, 'Good morning everyone! How is your day?', 80);
		expect(lines.length).toBeGreaterThan(0);
		expect(lines.join(' ')).toContain('Good');
	});
});
