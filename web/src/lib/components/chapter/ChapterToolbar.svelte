<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { ripple } from '$lib/actions/ripple';
	import { Button } from '$lib/components/ui';
	import ArrowLeft from 'lucide-svelte/icons/arrow-left';
	import Upload from 'lucide-svelte/icons/upload';
	import Download from 'lucide-svelte/icons/download';
	import Play from 'lucide-svelte/icons/play';
	import RotateCcw from 'lucide-svelte/icons/rotate-ccw';
	import LayoutGrid from 'lucide-svelte/icons/layout-grid';
	import BookOpen from 'lucide-svelte/icons/book-open';
	import Columns from 'lucide-svelte/icons/columns';
	import Scissors from 'lucide-svelte/icons/scissors';
	import Sparkles from 'lucide-svelte/icons/sparkles';
	import Square from 'lucide-svelte/icons/square';
	import Pencil from 'lucide-svelte/icons/pencil';
	import FileX from 'lucide-svelte/icons/file-x';

	export let bookId: string;
	export let chapterId: number;
	export let chapterSeq: number;
	export let chapterTitle: string | null = null;
	export let chapterTitleTarget: string | null = null;
	export let totalPages: number = 0;
	export let running: boolean = false;
	export let uploading: boolean = false;
	export let activeViewMode: 'reader' | 'grid' | 'compare' = 'reader';
	export let webtoonKind: 'output' | 'original' = 'output';
	export let webtoonWidth: 'sm' | 'md' | 'lg' = 'md';

	const dispatch = createEventDispatcher<{
		translate: void;
		cancel: void;
		clearProgress: void;
		clearAllPages: void;
		openReslice: void;
		editChapter: void;
		upload: FileList;
		changeViewMode: 'reader' | 'grid' | 'compare';
		changeWebtoonKind: 'output' | 'original';
		changeWebtoonWidth: 'sm' | 'md' | 'lg';
	}>();

	const WIDTH_OPTIONS = ['sm', 'md', 'lg'] as const;

	let fileInput: HTMLInputElement;

	function handleFileChange(e: Event) {
		const target = e.target as HTMLInputElement;
		if (target.files && target.files.length > 0) {
			dispatch('upload', target.files);
			target.value = '';
		}
	}
</script>

<div class="flex flex-col gap-4">
	<!-- TOP BREADCRUMB & PRIMARY ACTION BAR -->
	<div class="flex flex-wrap items-center justify-between gap-3">
		<div class="flex items-center gap-3">
			<a
				href={`/app/books/${bookId}/`}
				class="flex h-9 w-9 items-center justify-center rounded-xl border border-black/10 text-current opacity-70 transition hover:bg-black/5 hover:opacity-100 dark:border-white/10 dark:hover:bg-white/5"
				use:ripple
				aria-label="Back to Book"
			>
				<ArrowLeft size={16} />
			</a>

			<div>
				<div class="flex items-center gap-2 flex-wrap">
					<h1 class="text-lg font-bold tracking-tight">
						{#if chapterTitleTarget}
							{#if /^(chapter|ch\.?|ep\.?|第|\d+)/i.test(chapterTitleTarget.trim())}
								{chapterTitleTarget}
							{:else}
								Chapter {chapterSeq + 1}: {chapterTitleTarget}
							{/if}
							{#if chapterTitle && chapterTitle !== chapterTitleTarget}
								<span class="text-sm font-normal opacity-70 ml-1.5">({chapterTitle})</span>
							{/if}
						{:else if chapterTitle}
							{#if /^(chapter|ch\.?|ep\.?|第|\d+)/i.test(chapterTitle.trim())}
								{chapterTitle}
							{:else}
								Chapter {chapterSeq + 1}: {chapterTitle}
							{/if}
						{:else}
							Chapter {chapterSeq + 1}
						{/if}
					</h1>
					<button
						type="button"
						on:click={() => dispatch('editChapter')}
						class="rounded-md p-1 opacity-50 transition hover:bg-black/5 hover:opacity-100 dark:hover:bg-white/5"
						title="Edit chapter title & sequence"
						use:ripple
					>
						<Pencil size={13} />
					</button>
					<span class="rounded-md bg-black/5 px-2 py-0.5 text-xs font-semibold opacity-60 dark:bg-white/5">
						{totalPages} page{totalPages === 1 ? '' : 's'}
					</span>
				</div>
			</div>
		</div>

		<!-- ACTIONS -->
		<div class="flex flex-wrap items-center gap-2">
			<!-- UPLOAD BUTTON -->
			<input
				type="file"
				accept="image/*"
				multiple
				class="hidden"
				bind:this={fileInput}
				on:change={handleFileChange}
			/>

			<Button
				variant="secondary"
				size="sm"
				disabled={uploading || running}
				on:click={() => fileInput?.click()}
			>
				<Upload size={14} />
				<span>{uploading ? 'Uploading...' : 'Add Images'}</span>
			</Button>

			<!-- RESLICE -->
			{#if totalPages > 0}
				<Button
					variant="secondary"
					size="sm"
					disabled={running}
					on:click={() => dispatch('openReslice')}
				>
					<Scissors size={14} />
					<span class="hidden sm:inline">Reslice</span>
				</Button>
			{/if}

			<!-- CLEAR PROGRESS -->
			{#if totalPages > 0}
				<Button
					variant="secondary"
					size="sm"
					disabled={running}
					on:click={() => dispatch('clearProgress')}
				>
					<RotateCcw size={14} />
					<span class="hidden sm:inline">Clear Progress</span>
				</Button>
			{/if}

			<!-- CLEAR ALL PAGES -->
			{#if totalPages > 0}
				<Button
					variant="secondary"
					size="sm"
					disabled={running}
					class="text-red-600 hover:bg-red-500/10 dark:text-red-400"
					on:click={() => dispatch('clearAllPages')}
				>
					<FileX size={14} />
					<span class="hidden sm:inline">Clear Pages</span>
				</Button>
			{/if}

			<!-- TRANSLATE / CANCEL ACTIONS -->
			{#if totalPages > 0}
				{#if running}
					<Button
						variant="danger"
						size="sm"
						on:click={() => dispatch('cancel')}
					>
						<Square size={12} class="fill-current" />
						<span>Cancel</span>
					</Button>
				{:else}
					<Button
						variant="primary"
						size="sm"
						on:click={() => dispatch('translate')}
					>
						<Play size={14} />
						<span>Translate All</span>
					</Button>
				{/if}
			{/if}

			<!-- DOWNLOAD ZIP -->
			{#if totalPages > 0}
				<a
					href={`/api/chapters/${chapterId}/download`}
					class="inline-flex items-center gap-1.5 rounded-lg border border-black/10 px-3 py-1.5 text-xs font-medium transition hover:bg-black/5 dark:border-white/10 dark:hover:bg-white/5"
					download
					use:ripple
				>
					<Download size={14} />
					<span class="hidden sm:inline">Export ZIP</span>
				</a>
			{/if}
		</div>
	</div>

	<!-- SECONDARY TOOLBAR: VIEW MODE TABS + WEBTOON CONFIG -->
	{#if totalPages > 0}
		<div class="flex flex-wrap items-center justify-between gap-3 border-y border-black/[0.06] py-2 dark:border-white/[0.06]">
			<!-- VIEW MODES -->
			<div class="flex items-center gap-1 rounded-xl bg-black/[0.04] p-1 dark:bg-white/[0.04]">
				<button
					type="button"
					on:click={() => dispatch('changeViewMode', 'reader')}
					class={`flex items-center gap-1.5 rounded-lg px-3 py-1 text-xs font-medium transition ${
						activeViewMode === 'reader'
							? 'bg-white text-black shadow-sm dark:bg-[#201c18] dark:text-white'
							: 'opacity-60 hover:opacity-100'
					}`}
					use:ripple
				>
					<BookOpen size={13} />
					<span>Webtoon</span>
				</button>

				<button
					type="button"
					on:click={() => dispatch('changeViewMode', 'grid')}
					class={`flex items-center gap-1.5 rounded-lg px-3 py-1 text-xs font-medium transition ${
						activeViewMode === 'grid'
							? 'bg-white text-black shadow-sm dark:bg-[#201c18] dark:text-white'
							: 'opacity-60 hover:opacity-100'
					}`}
					use:ripple
				>
					<LayoutGrid size={13} />
					<span>Grid</span>
				</button>

				<button
					type="button"
					on:click={() => dispatch('changeViewMode', 'compare')}
					class={`flex items-center gap-1.5 rounded-lg px-3 py-1 text-xs font-medium transition ${
						activeViewMode === 'compare'
							? 'bg-white text-black shadow-sm dark:bg-[#201c18] dark:text-white'
							: 'opacity-60 hover:opacity-100'
					}`}
					use:ripple
				>
					<Columns size={13} />
					<span>Compare</span>
				</button>
			</div>

			<!-- WEBTOON CONTROLS -->
			{#if activeViewMode === 'reader'}
				<div class="flex items-center gap-2">
					<!-- OUTPUT / ORIGINAL TOGGLE -->
					<div class="flex items-center rounded-lg border border-black/10 p-0.5 text-xs dark:border-white/10">
						<button
							type="button"
							on:click={() => dispatch('changeWebtoonKind', 'output')}
							class={`rounded-md px-2 py-0.5 font-medium transition ${
								webtoonKind === 'output'
									? 'bg-[#b23a2e] text-white shadow-xs'
									: 'opacity-60 hover:opacity-100'
							}`}
						>
							Translated
						</button>
						<button
							type="button"
							on:click={() => dispatch('changeWebtoonKind', 'original')}
							class={`rounded-md px-2 py-0.5 font-medium transition ${
								webtoonKind === 'original'
									? 'bg-[#b23a2e] text-white shadow-xs'
									: 'opacity-60 hover:opacity-100'
							}`}
						>
							Original
						</button>
					</div>

					<!-- WIDTH SELECTOR -->
					<div class="hidden sm:flex items-center gap-1 rounded-lg border border-black/10 p-0.5 text-xs dark:border-white/10">
						{#each WIDTH_OPTIONS as w}
							<button
								type="button"
								on:click={() => dispatch('changeWebtoonWidth', w)}
								class={`rounded-md px-2 py-0.5 uppercase font-medium transition ${
									webtoonWidth === w
										? 'bg-black/10 font-bold dark:bg-white/10'
										: 'opacity-50 hover:opacity-100'
								}`}
							>
								{w}
							</button>
						{/each}
					</div>
				</div>
			{/if}

		</div>
	{/if}
</div>
