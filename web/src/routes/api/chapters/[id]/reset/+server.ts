// CLEAR ALL PAGES' PROGRESS IN A CHAPTER — REGIONS, CACHED TRANSLATIONS, AND OUTPUTS.
import { error, json } from '@sveltejs/kit';
import { resetChapterProgress } from '$lib/server/chapters';
import type { RequestHandler } from './$types';

export const POST: RequestHandler = async ({ params }) => {
	const chapterId = Number(params.id);
	if (!Number.isInteger(chapterId)) throw error(400, 'Invalid chapter id.');

	const reset = resetChapterProgress(chapterId);
	return json({ ok: true, reset });
};
