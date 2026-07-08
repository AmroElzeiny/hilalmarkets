//add content seo tab
#product-tabs|append = from(/public/plugins/seo/seotab.html|#seo-tab > .nav-item)
#post-tabs|append    = from(/public/plugins/seo/seotab.html|#seo-tab > .nav-item)

#product-tab-content|append = from(/public/plugins/seo/seotab.html|#seo-tab-content > .tab-pane)
#post-tab-content|append    = from(/public/plugins/seo/seotab.html|#seo-tab-content > .tab-pane)



#site-tabs|append    = from(/public/plugins/seo/seotab.html|#seo-tab > .nav-item)
#site-tab-content|append = from(/public/plugins/seo/seotab.html|#seo-tab-content > .tab-pane)


[data-v-seo-*] = <?php 
	$key = '@@__name:seo\[([^\]]+)\]__@@';
	$name = '@@__data-v-seo-(*)__@@';
	$default = $_POST[$language['language_id']][$key][$name] ?? '';
	echo htmlspecialchars($this->seo[$language['language_id']][$key][$name] ?? $default);
?>

[data-v-seo-*]|placeholder = <?php 
	$key = '@@__name:seo\[([^\]]+)\]__@@';
	$name = '@@__data-v-seo-(*)__@@';
	
	if (isset($this->post['post_content'][$language['language_id']])) {
		if ($name == 'title') {
			echo htmlspecialchars($this->post['post_content'][$language['language_id']]['name'] ?? '');
		} 	
		
		if ($name == 'description' || $name == 'content') {
			echo htmlspecialchars($this->post['post_content'][$language['language_id']]['meta_description'] ?:
				  $this->post['post_content'][$language['language_id']]['excerpt'] ?: 
				  substr(strip_tags($this->post['post_content'][$language['language_id']]['content'] ?? ''), 0, 255));
		} 
	} 

?>

[data-v-seo-*]|name = <?php 
	$key = '@@__name:seo\[([^\]]+)\]__@@';
	$name = '@@__data-v-seo-(*)__@@';
	echo "seo[$key][{$language['language_id']}][$name]";
?>

