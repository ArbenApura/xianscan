// ML SIDECAR HTTP CLIENT — THE WEB APP'S ONLY TOUCHPOINT WITH THE PYTHON SERVICE.
//
// DESIGN: THE PipelineClient INTERFACE IS WHAT THE CHAPTER RUNNER DEPENDS ON — TESTS INJECT A FAKE;
// PRODUCTION USES HttpPipelineClient (ML_BASE_URL FROM ENV). THE FAKE NEEDS NO MOCKS AT ALL.
import { env } from '$env/dynamic/private';

// -- TYPES -- //

export interface PipelineBox {
	x: number;
	y: number;
	w: number;
	h: number;
}

export interface PipelineRegion {
	id: string;
	box: PipelineBox;
	polygon: number[][];
	category: 'dialogue' | 'sfx' | 'mono' | 'other';
	text: string;
	confidence: number;
	vertical: boolean;
}

export interface AnalyzeResult {
	width: number;
	height: number;
	backend: string;
	regions: PipelineRegion[];
}

export interface CleanRegionInput {
	id: string;
	box: PipelineBox;
	polygon: number[][];
}

export interface PipelineClient {
	preprocess(image: Buffer, signal?: AbortSignal): Promise<Buffer>;
	analyze(image: Buffer, signal?: AbortSignal): Promise<AnalyzeResult>;
	clean(image: Buffer, regions: CleanRegionInput[], signal?: AbortSignal): Promise<Buffer>;
	health(): Promise<{ status: string; detector: string; inpainter: string }>;
	stitch?(imageTop: Buffer, imageBottom: Buffer, signal?: AbortSignal): Promise<Buffer>;
}

// -- ERRORS -- //

export class PipelineError extends Error {
	constructor(
		message: string,
		readonly status: number,
	) {
		super(message);
		this.name = 'PipelineError';
	}
}

// -- HTTP IMPLEMENTATION -- //

export class HttpPipelineClient implements PipelineClient {
	constructor(
		private readonly baseUrl: string,
		private readonly fetchImpl: typeof fetch = fetch,
	) {
		// NORMALISE: NEVER BUILD `//pages/...` FROM A TRAILING-SLASH BASE URL
		this.baseUrl = baseUrl.replace(/\/+$/, '');
	}

	private async request(path: string, init: RequestInit, signal?: AbortSignal): Promise<Response> {
		try {
			return await this.fetchImpl(`${this.baseUrl}${path}`, { ...init, signal });
		} catch (e) {
			if (e instanceof PipelineError) throw e;
			// NETWORK / ABORT — SURFACE AS A CLEAR SIDECAR-DOWN ERROR
			if ((e as { name?: string })?.name === 'AbortError') throw e;
			throw new PipelineError(`ML sidecar unreachable at ${this.baseUrl}${path}: ${(e as Error).message}`, 0);
		}
	}

	async preprocess(image: Buffer, signal?: AbortSignal): Promise<Buffer> {
		const form = new FormData();
		form.append('image', new Blob([new Uint8Array(image)]), 'page.png');
		const resp = await this.request('/pages/preprocess', { method: 'POST', body: form }, signal);
		if (!resp.ok) throw new PipelineError(`preprocess failed (${resp.status}): ${await resp.text()}`, resp.status);
		return Buffer.from(await resp.arrayBuffer());
	}

	async analyze(image: Buffer, signal?: AbortSignal): Promise<AnalyzeResult> {
		const form = new FormData();
		form.append('image', new Blob([new Uint8Array(image)]), 'page.png');
		const resp = await this.request('/pages/analyze', { method: 'POST', body: form }, signal);
		if (!resp.ok) throw new PipelineError(`analyze failed (${resp.status}): ${await resp.text()}`, resp.status);
		return (await resp.json()) as AnalyzeResult;
	}

	async clean(image: Buffer, regions: CleanRegionInput[], signal?: AbortSignal): Promise<Buffer> {
		const form = new FormData();
		form.append('image', new Blob([new Uint8Array(image)]), 'page.png');
		form.append('regions', JSON.stringify(regions));
		const resp = await this.request('/pages/clean', { method: 'POST', body: form }, signal);
		if (!resp.ok) throw new PipelineError(`clean failed (${resp.status}): ${await resp.text()}`, resp.status);
		return Buffer.from(await resp.arrayBuffer());
	}

	async stitch(imageTop: Buffer, imageBottom: Buffer, signal?: AbortSignal): Promise<Buffer> {
		const form = new FormData();
		form.append('image_top', new Blob([new Uint8Array(imageTop)]), 'top.png');
		form.append('image_bottom', new Blob([new Uint8Array(imageBottom)]), 'bottom.png');
		const resp = await this.request('/pages/stitch', { method: 'POST', body: form }, signal);
		if (!resp.ok) throw new PipelineError(`stitch failed (${resp.status}): ${await resp.text()}`, resp.status);
		return Buffer.from(await resp.arrayBuffer());
	}


	async health(): Promise<{ status: string; detector: string; inpainter: string }> {
		const resp = await this.request('/health', { method: 'GET' });
		if (!resp.ok) throw new PipelineError(`health failed (${resp.status})`, resp.status);
		return (await resp.json()) as { status: string; detector: string; inpainter: string };
	}
}

// THE PRODUCTION SINGLETON — FAILS FAST AT CREATION WHEN THE SIDECAR ISN'T CONFIGURED.
export function createPipelineClient(): PipelineClient {
	const baseUrl = env.ML_BASE_URL ?? '';
	if (!baseUrl) {
		throw new Error('ML_BASE_URL is not set — start the ML sidecar (ml/README) and set ML_BASE_URL=http://127.0.0.1:8001');
	}
	return new HttpPipelineClient(baseUrl.replace(/\/+$/, ''));
}
