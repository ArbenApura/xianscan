// CLEAR ONE PAGE'S PROGRESS — REGIONS, CACHED TRANSLATIONS, AND OUTPUTS — SO A RE-RUN STARTS FRESH.
import { error, json } from '@sveltejs/kit';
import { resetPageProgress } from '$lib/server/chapters';
import type { RequestHandler } from './$types';

export const POST: RequestHandler = async ({ params }) => {
	const pageId = Number(params.id);
	if (!Number.isInteger(pageId)) throw error(400, 'Invalid page id.');

	resetPageProgress(pageId);
	return json({ ok: true });
};
