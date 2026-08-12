// WATERMARK DETECTION & FILTERING TESTS (TDD)
import { describe, expect, it } from 'vitest';
import {
	isWatermarkText,
	filterWatermarkRegions,
	DEFAULT_WATERMARK_PATTERNS,
} from '$lib/server/watermark';
import type { PipelineRegion } from '$lib/server/pipeline-client';

describe('watermark detection & filtering', () => {
	it('detects common site URLs and domain watermarks', () => {
		expect(isWatermarkText('www.baozimh.com')).toBe(true);
		expect(isWatermarkText('https://bilibili.com/manga')).toBe(true);
		expect(isWatermarkText('manga123.cc')).toBe(true);
		expect(isWatermarkText('sub.domain.org')).toBe(true);
	});

	it('detects common scanlation & Chinese site signatures', () => {
		expect(isWatermarkText('包子漫畫 獨家首發')).toBe(true);
		expect(isWatermarkText('快看漫画 严禁转载')).toBe(true);
		expect(isWatermarkText('嗶哩嗶哩漫畫')).toBe(true);
		expect(isWatermarkText('关注公众号: 某某汉化组')).toBe(true);
		expect(isWatermarkText('QQ群: 12345678')).toBe(true);
	});

	it('does not flag normal dialogue as watermarks', () => {
		expect(isWatermarkText('你好，少年！')).toBe(false);
		expect(isWatermarkText('这一剑，斩断过去！')).toBe(false);
		expect(isWatermarkText('Wait for me!')).toBe(false);
		expect(isWatermarkText('Boom!!')).toBe(false);
	});

	it('supports custom user-defined watermark patterns', () => {
		const custom = ['custom-scan', 'xian-site'];
		expect(isWatermarkText('Downloaded from custom-scan', custom)).toBe(true);
		expect(isWatermarkText('Visit xian-site today', custom)).toBe(true);
		expect(isWatermarkText('Normal text', custom)).toBe(false);
	});

	it('correctly partitions pipeline regions into translation vs watermark regions', () => {
		const sampleRegions: PipelineRegion[] = [
			{
				id: 'r0',
				box: { x: 10, y: 10, w: 100, h: 50 },
				polygon: [[10, 10]],
				category: 'dialogue',
				text: '这一剑！',
				confidence: 0.95,
				vertical: false,
			},
			{
				id: 'r1',
				box: { x: 500, y: 10, w: 200, h: 30 },
				polygon: [[500, 10]],
				category: 'other',
				text: 'www.baozimh.com',
				confidence: 0.90,
				vertical: false,
			},
			{
				id: 'r2',
				box: { x: 50, y: 200, w: 80, h: 30 },
				polygon: [[50, 200]],
				category: 'sfx',
				text: '轰！',
				confidence: 0.88,
				vertical: false,
			},
		];

		const { textRegions, watermarkRegions } = filterWatermarkRegions(sampleRegions);
		expect(textRegions).toHaveLength(2);
		expect(textRegions.map((r) => r.id)).toEqual(['r0', 'r2']);

		expect(watermarkRegions).toHaveLength(1);
		expect(watermarkRegions[0].id).toBe('r1');
		expect(watermarkRegions[0].text).toBe('www.baozimh.com');
	});

	it('splits mixed multi-line regions so legitimate text is preserved and watermark lines are separated', () => {
		const sampleRegions: PipelineRegion[] = [
			{
				id: 'r120',
				box: { x: 500, y: 400, w: 300, h: 120 },
				polygon: [[500, 400]],
				category: 'sfx',
				text: '*食我压路机哒！\nACLOUDMERGE.COM\nCOLAMANGA.COM',
				confidence: 0.92,
				vertical: false,
			},
		];

		const { textRegions, watermarkRegions } = filterWatermarkRegions(sampleRegions);

		// The legitimate line *食我压路机哒！ becomes a text region for translation & typesetting
		expect(textRegions).toHaveLength(1);
		expect(textRegions[0].text).toBe('*食我压路机哒！');
		expect(textRegions[0].category).toBe('sfx');
		expect(textRegions[0].box.y).toBe(400);

		// The watermark lines ACLOUDMERGE.COM & COLAMANGA.COM become a separate watermark region for erasure
		expect(watermarkRegions).toHaveLength(1);
		expect(watermarkRegions[0].text).toBe('ACLOUDMERGE.COM\nCOLAMANGA.COM');
		expect(watermarkRegions[0].category).toBe('other');
		expect(watermarkRegions[0].box.y).toBeGreaterThan(400);
	});
});
