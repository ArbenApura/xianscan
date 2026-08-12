<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { ripple } from '$lib/actions/ripple';
	import { Button } from '$lib/components/ui';
	import Sparkles from 'lucide-svelte/icons/sparkles';
	import BookOpen from 'lucide-svelte/icons/book-open';
	import Languages from 'lucide-svelte/icons/languages';
	import Cpu from 'lucide-svelte/icons/cpu';
	import ArrowRight from 'lucide-svelte/icons/arrow-right';

	onMount(() => {
		// Automatically navigate to app dashboard if user doesn't interact in 2.5s
		const t = setTimeout(() => {
			goto('/app/');
		}, 2500);
		return () => clearTimeout(t);
	});
</script>

<svelte:head>
	<title>Manua Translator — Self-hosted Manhua Translation</title>
</svelte:head>

<div class="relative flex min-h-screen flex-col items-center justify-center px-4 py-12 text-center">
	<!-- BACKGROUND GLOW EFFECTS -->
	<div class="pointer-events-none absolute inset-0 overflow-hidden">
		<div class="absolute left-1/2 top-1/4 -translate-x-1/2 -translate-y-1/2 w-96 h-96 rounded-full bg-[#b23a2e]/10 blur-3xl"></div>
		<div class="absolute right-1/4 bottom-1/4 w-80 h-80 rounded-full bg-amber-500/10 blur-3xl"></div>
	</div>

	<!-- HERO BADGE -->
	<div class="relative z-10 mb-6 inline-flex items-center gap-2 rounded-full border border-[#b23a2e]/20 bg-[#b23a2e]/10 px-3.5 py-1.5 text-xs font-semibold text-[#b23a2e] dark:text-[#e08a63]">
		<Sparkles size={14} />
		<span>Self-hosted Manhua Translation Pipeline</span>
	</div>

	<!-- MAIN TITLE -->
	<h1 class="relative z-10 max-w-3xl text-4xl font-extrabold tracking-tight sm:text-5xl lg:text-6xl">
		Manua <span class="bg-gradient-to-r from-[#b23a2e] to-amber-600 bg-clip-text text-transparent dark:from-[#e08a63] dark:to-amber-400">Translator</span>
	</h1>

	<!-- SUBTITLE -->
	<p class="relative z-10 mt-4 max-w-xl text-base opacity-70 sm:text-lg">
		End-to-end comic translation: text detection, OCR, DeepSeek translation with custom glossaries, LaMa text erasing, and Skia typesetting.
	</p>

	<!-- CTA BUTTONS -->
	<div class="relative z-10 mt-8 flex flex-wrap items-center justify-center gap-4">
		<Button href="/app/" variant="primary" size="md" class="px-6 py-3 text-base shadow-lg shadow-[#b23a2e]/20">
			Launch Library <ArrowRight size={18} class="ml-1" />
		</Button>
		<Button href="/app/glossary" variant="secondary" size="md" class="px-6 py-3 text-base">
			Manage Glossary
		</Button>
	</div>

	<!-- FEATURE HIGHLIGHTS -->
	<div class="relative z-10 mt-16 grid w-full max-w-4xl grid-cols-1 gap-4 sm:grid-cols-3 text-left">
		<div class="rounded-xl border border-black/[0.06] bg-white/60 p-5 backdrop-blur dark:border-white/[0.06] dark:bg-white/[0.03]">
			<div class="mb-3 inline-flex rounded-lg bg-[#b23a2e]/10 p-2.5 text-[#b23a2e] dark:text-[#e08a63]">
				<Cpu size={20} />
			</div>
			<h2 class="font-semibold">Local ML Sidecar</h2>
			<p class="mt-1 text-xs opacity-60">ONNX comic-text-detector + RapidOCR + LaMa inpainting running locally on CPU.</p>
		</div>

		<div class="rounded-xl border border-black/[0.06] bg-white/60 p-5 backdrop-blur dark:border-white/[0.06] dark:bg-white/[0.03]">
			<div class="mb-3 inline-flex rounded-lg bg-amber-500/10 p-2.5 text-amber-600 dark:text-amber-400">
				<Languages size={20} />
			</div>
			<h2 class="font-semibold">DeepSeek & Glossary</h2>
			<p class="mt-1 text-xs opacity-60">High-accuracy LLM translation with Aho-Corasick glossary term injection & caching.</p>
		</div>

		<div class="rounded-xl border border-black/[0.06] bg-white/60 p-5 backdrop-blur dark:border-white/[0.06] dark:bg-white/[0.03]">
			<div class="mb-3 inline-flex rounded-lg bg-emerald-500/10 p-2.5 text-emerald-600 dark:text-emerald-400">
				<BookOpen size={20} />
			</div>
			<h2 class="font-semibold">Typesetting & Zip Export</h2>
			<p class="mt-1 text-xs opacity-60">Canvas rendering with OFL fonts, webtoon continuous reader, and chapter zip export.</p>
		</div>
	</div>

	<div class="relative z-10 mt-12 text-xs opacity-40">
		Redirecting to library automatically... <a href="/app/" class="underline hover:text-[#b23a2e]">Click here if not redirected</a>
	</div>
</div>
