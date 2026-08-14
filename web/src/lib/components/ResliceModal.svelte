<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { streamSse, type SseEvent } from '$lib/sse';
	import { toast } from 'svelte-sonner';
	import Scissors from 'lucide-svelte/icons/scissors';
	import CheckCircle2 from 'lucide-svelte/icons/check-circle-2';
	import AlertCircle from 'lucide-svelte/icons/alert-circle';
	import Sparkles from 'lucide-svelte/icons/sparkles';
	import X from 'lucide-svelte/icons/x';
	import { Button, Modal } from '$lib/components/ui';

	export let open = false;
	export let chapterId: number;
	export let pageCount: number;

	const dispatch = createEventDispatcher<{
		complete: { originalCount: number; newCount: number };
		close: void;
	}>();

	type State = 'idle' | 'running' | 'done' | 'error';
	type StepId = 'read' | 'reslice' | 'save';

	let state: State = 'idle';
	let message = 'Preparing chapter images...';
	let originalCount = pageCount;
	let newCount = pageCount;
	let errorMessage = '';

	let stepStatus: Record<StepId, 'pending' | 'active' | 'done'> = {
		read: 'pending',
		reslice: 'pending',
		save: 'pending',
	};

	let abortController: AbortController | null = null;

	const STEPS: Array<{ id: StepId; label: string; desc: string }> = [
		{ id: 'read', label: 'Assemble Images', desc: 'Read and stitch raw chapter slices' },
		{ id: 'reslice', label: 'Protect Text & Find Gutters', desc: 'Cluster dialogue bubbles and locate panel breaks' },
		{ id: 'save', label: 'Save Clean Pages', desc: 'Write re-sliced images and update chapter database' },
	];

	function resetStepStatuses() {
		stepStatus = {
			read: 'pending',
			reslice: 'pending',
			save: 'pending',
		};
	}

	function updateStepFromBackend(step: StepId, msg: string) {
		if (step === 'read') {
			stepStatus = { read: 'active', reslice: 'pending', save: 'pending' };
		} else if (step === 'reslice') {
			stepStatus = { read: 'done', reslice: 'active', save: 'pending' };
		} else if (step === 'save') {
			stepStatus = { read: 'done', reslice: 'done', save: 'active' };
		}
		if (msg) message = msg;
	}

	export function start() {
		state = 'running';
		resetStepStatuses();
		stepStatus.read = 'active';
		message = `Reading ${pageCount} chapter image slices...`;
		errorMessage = '';
		abortController = new AbortController();

		streamSse(
			`/api/chapters/${chapterId}/reslice`,
			{},
			(e: SseEvent) => {
				if (e.type === 'start') {
					updateStepFromBackend('read', `Reading ${pageCount} chapter image slices...`);
				} else if (e.type === 'progress') {
					const backendStep = (e.step as StepId) || 'read';
					const backendMsg = (e.message as string) || message;
					updateStepFromBackend(backendStep, backendMsg);
				} else if (e.type === 'done') {
					originalCount = (e.originalCount as number) || pageCount;
					newCount = (e.newCount as number) || originalCount;
					const finalMsg = (e.message as string) || 'Chapter successfully re-sliced!';

					// MARK ALL STEPS AS COMPLETED
					stepStatus = { read: 'done', reslice: 'done', save: 'done' };
					message = finalMsg;

					// BRIEF PAUSE SO THE USER SEES ALL 3 GREEN CHECKMARKS BEFORE SUMMARY
					setTimeout(() => {
						state = 'done';
						toast.success(`Smart Re-slice complete: ${originalCount} slices → ${newCount} clean pages.`);
					}, 600);
				} else if (e.type === 'error') {
					state = 'error';
					errorMessage = (e.message as string) || 'Re-slicing failed.';
					toast.error(errorMessage);
				}
			},
			abortController.signal,
		).catch((err) => {
			if (abortController?.signal.aborted) {
				state = 'idle';
				resetStepStatuses();
				toast.info('Re-slicing cancelled.');
				handleClose();
			} else {
				state = 'error';
				errorMessage = err instanceof Error ? err.message : String(err);
				toast.error(errorMessage);
			}
		});
	}

	function cancel() {
		if (abortController) {
			abortController.abort();
		}
		state = 'idle';
		resetStepStatuses();
		handleClose();
	}

	function handleClose() {
		if (state === 'running') return;
		const wasDone = state === 'done';
		open = false;
		state = 'idle';
		resetStepStatuses();
		if (wasDone) {
			dispatch('complete', { originalCount, newCount });
		} else {
			dispatch('close');
		}
	}
</script>

<Modal
	bind:open
	title="Smart Webtoon Re-slicing"
	size="md"
	closable={state !== 'running'}
	on:close={handleClose}
>
	<!-- HEADER ICON & BADGE -->
	<div class="flex items-center gap-3">
		<div class="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[#b23a2e]/10 text-[#b23a2e] dark:bg-[#e08a63]/10 dark:text-[#e08a63]">
			{#if state === 'done'}
				<CheckCircle2 size={22} class="text-emerald-600 dark:text-emerald-400" />
			{:else if state === 'error'}
				<AlertCircle size={22} class="text-rose-600 dark:text-rose-400" />
			{:else}
				<Scissors size={20} class={state === 'running' ? 'animate-pulse' : ''} />
			{/if}
		</div>
		<div>
			<div class="text-xs font-bold leading-tight">Continuous Canvas Re-slicer</div>
			<div class="text-[11px] opacity-60">Stitch strip & cut at true inter-panel margins</div>
		</div>
	</div>

	<!-- BODY: IDLE STATE -->
	{#if state === 'idle'}
		<div class="mt-4 space-y-3 text-xs">
			<div class="rounded-xl border border-black/[0.08] bg-black/[0.02] p-3.5 dark:border-white/[0.08] dark:bg-white/[0.02]">
				<div class="flex items-start gap-2.5">
					<Sparkles size={16} class="mt-0.5 shrink-0 text-[#b23a2e] dark:text-[#e08a63]" />
					<div class="space-y-1">
						<p class="font-semibold">Why use Smart Re-slicing?</p>
						<p class="opacity-70 leading-relaxed">
							Raw scrapers often slice images arbitrarily, splitting speech bubbles and dividing conversations.
							This tool merges all {pageCount} slices, clusters dialogue to prevent mid-scene cuts, and slices only along empty panel margins.
						</p>
					</div>
				</div>
			</div>

			<p class="text-[11px] opacity-60">
				⚠️ This replaces raw slices with cleanly divided pages. Existing translation progress on this chapter will be reset.
			</p>
		</div>

		<div class="mt-6 flex items-center justify-end gap-2">
			<Button variant="ghost" size="sm" on:click={handleClose}>Cancel</Button>
			<Button variant="primary" size="sm" on:click={start}>
				<Scissors size={13} /> Start Re-slicing
			</Button>
		</div>

	<!-- BODY: RUNNING LIVE PROGRESS STATE (100% REAL-TIME BACKEND SSE DRIVEN) -->
	{:else if state === 'running'}
		<div class="mt-5 space-y-4">
			<!-- STATUS & SPINNER -->
			<div class="flex items-center justify-between text-xs font-semibold">
				<span class="flex items-center gap-2 text-[#b23a2e] dark:text-[#e08a63]">
					<span class="spinner-dot"></span> In Progress...
				</span>
				<span class="text-[11px] font-normal opacity-50">Please do not close</span>
			</div>

			<!-- CURRENT STATUS MESSAGE BANNER FROM BACKEND -->
			<div class="rounded-lg border border-black/[0.06] bg-black/[0.02] p-3 text-center text-xs font-medium dark:border-white/[0.06] dark:bg-white/[0.02]">
				{message}
			</div>

			<!-- STEP STATUS CHECKLIST WITH LIVE ROTATING SPINNERS -->
			<div class="space-y-2 pt-1 text-xs">
				{#each STEPS as step, idx}
					{@const status = stepStatus[step.id]}
					<div
						class={`flex items-center gap-3 rounded-lg px-3 py-2.5 transition-all duration-300 ${
							status === 'active'
								? 'bg-[#b23a2e]/10 text-[#b23a2e] dark:bg-[#e08a63]/10 dark:text-[#e08a63] font-semibold ring-1 ring-[#b23a2e]/30 dark:ring-[#e08a63]/30 shadow-sm'
								: status === 'done'
									? 'opacity-90 bg-black/[0.02] dark:bg-white/[0.02]'
									: 'opacity-40'
						}`}
					>
						<!-- STEP INDICATOR ICON -->
						{#if status === 'done'}
							<CheckCircle2 size={18} class="shrink-0 text-emerald-600 dark:text-emerald-400" />
						{:else if status === 'active'}
							<span class="spinner-ring shrink-0"></span>
						{:else}
							<span class="flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full border border-current text-[10px] font-bold opacity-60">
								{idx + 1}
							</span>
						{/if}

						<div>
							<div class="text-[11px] font-bold leading-tight">{step.label}</div>
							<div class="text-[10px] opacity-70">{step.desc}</div>
						</div>
					</div>
				{/each}
			</div>

			<div class="mt-4 flex items-center justify-between border-t border-black/[0.06] pt-3 text-[11px] opacity-60 dark:border-white/[0.06]">
				<span>Dialog is locked until completion</span>
				<Button variant="secondary" size="sm" on:click={cancel}>
					<X size={12} /> Cancel Process
				</Button>
			</div>
		</div>

	<!-- BODY: DONE STATE -->
	{:else if state === 'done'}
		<div class="mt-5 space-y-4 text-xs">
			<div class="rounded-xl border border-emerald-500/20 bg-emerald-500/10 p-4 text-emerald-900 dark:text-emerald-200">
				<h4 class="font-bold text-sm">Re-slicing Complete!</h4>
				<p class="mt-1 text-[11px] opacity-80">{message}</p>
			</div>

			<div class="grid grid-cols-2 gap-3 text-center">
				<div class="rounded-lg border border-black/10 p-3 dark:border-white/10">
					<span class="block text-[10px] opacity-60">Original Slices</span>
					<span class="text-lg font-bold">{originalCount}</span>
				</div>
				<div class="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3 text-emerald-600 dark:text-emerald-400">
					<span class="block text-[10px] opacity-70">Clean Pages</span>
					<span class="text-lg font-bold">{newCount}</span>
				</div>
			</div>

			<div class="mt-6 flex justify-end">
				<Button variant="primary" size="sm" on:click={handleClose}>
					Done & Reload Chapter
				</Button>
			</div>
		</div>

	<!-- BODY: ERROR STATE -->
	{:else if state === 'error'}
		<div class="mt-5 space-y-4 text-xs">
			<div class="rounded-xl border border-rose-500/20 bg-rose-500/10 p-4 text-rose-900 dark:text-rose-200">
				<h4 class="font-bold text-sm">Re-slicing Failed</h4>
				<p class="mt-1 text-[11px] opacity-80">{errorMessage}</p>
			</div>

			<div class="mt-6 flex justify-end gap-2">
				<Button variant="ghost" size="sm" on:click={handleClose}>Close</Button>
				<Button variant="primary" size="sm" on:click={start}>Try Again</Button>
			</div>
		</div>
	{/if}
</Modal>

<style>
	@keyframes spin-anim {
		0% {
			transform: rotate(0deg);
		}
		100% {
			transform: rotate(360deg);
		}
	}

	.spinner-ring {
		display: inline-block;
		width: 18px;
		height: 18px;
		border: 2.5px solid rgba(178, 58, 46, 0.25);
		border-top-color: currentColor;
		border-radius: 50%;
		animation: spin-anim 0.85s linear infinite;
	}

	.spinner-dot {
		display: inline-block;
		width: 14px;
		height: 14px;
		border: 2px solid rgba(178, 58, 46, 0.2);
		border-top-color: currentColor;
		border-radius: 50%;
		animation: spin-anim 0.75s linear infinite;
	}
</style>
