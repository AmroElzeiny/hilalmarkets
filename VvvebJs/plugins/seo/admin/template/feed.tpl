[data-v-{{feed}}]|deleteAllButFirst

[data-v-{{feed}}]|before = <?php if (isset($this->seo['{{feed}}'])) foreach ($this->seo['{{feed}}'] as $feed) {?>

	[data-v-{{feed}}] a[data-v-{{feed}}-*]|href     = $feed['@@__data-v-{{feed}}-(*)__@@']
	[data-v-{{feed}}] [data-v-{{feed}}-*]|innerText = $feed['@@__data-v-{{feed}}-(*)__@@']

[data-v-{{feed}}]|after = <?php } ?>