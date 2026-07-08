[data-v-post-toc]|before = <?php $toc = $post['toc'] ?? $this->_component['post'][0]['toc'] ?? [];?>

[data-v-toc-list]|before = <?php if (!function_exists('display_toc')) {
	function display_toc($items) { $depth = 1;?>

	[data-v-toc-item]|deleteAllButFirst

	[data-v-toc-item]|before = <?php foreach($items as $toc_item) {
			
		if (is_array($toc_item)) { 
			//display_toc($toc_item);
		}

		if ($toc_item['depth'] < $depth) {
			echo '</ol>';
			$depth = $toc_item['depth'];
		}

		if ($toc_item['depth'] > $depth) {
			echo '<ol>';
			$depth = $toc_item['depth'];
		}
	?>
	
		[data-v-toc-item-url]|href = $toc_item['url']
		[data-v-toc-item-url]|onclick = $toc_item['onclick']
		[data-v-toc-item-name] = $toc_item['name']
	
	[data-v-toc-item]|after = <?php 
		if ($toc_item['depth'] < $depth) {
			//echo '</ol>';
			//$depth = $toc_item['depth'];
		}
	}?>

[data-v-toc-list]|after = <?php 
	}
}

if (isset($toc)) {
	display_toc($toc);
}
?>


