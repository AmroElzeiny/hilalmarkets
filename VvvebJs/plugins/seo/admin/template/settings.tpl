import(crud.tpl, {"type":"seo"})

.settings input[type="text"]|value = <?php 
	$_setting = trim('@@__name:(\[.*\])__@@', '[]');
	if ($_setting) {
		echo htmlspecialchars(\Vvveb\arrayPath($this->seo, $_setting, '', '][') ?? '@@__value__@@');
	}
?>

.settings input[type="password"]|value = <?php 
	$_setting = trim('@@__name:(\[.*\])__@@', '[]');
	if ($_setting) {
		echo htmlspecialchars(\Vvveb\arrayPath($this->seo, $_setting, '', '][') ?? '@@__value__@@');
	}
?>

.settings input[type="number"]|value = <?php 
	$_setting = trim('@@__name:(\[.*\])__@@', '[]');
	if ($_setting) {
		echo htmlspecialchars(\Vvveb\arrayPath($this->seo, $_setting, '', '][') ?? '@@__value__@@');
	}
?>

.settings input[type="radio"]|addNewAttribute = <?php 
	$_setting = '@@__name:\[(.*)\]__@@';
	$_value = '@@__value__@@';
	
	if (isset($_POST['seo'][$_setting]) && ($_POST['seo'][$_setting] == $_value) ||
		(Vvveb\getSetting('seo',$_setting, '') == $_value)  ||
		 '@@__checked__@@') { 
			echo 'checked';
	}
?>

.settings textarea = <?php 
	$_setting = trim('@@__name:(\[.*\])__@@', '[]');
	if ($_setting) {
		echo htmlspecialchars(\Vvveb\arrayPath($this->seo, $_setting, '', '][') ?? '@@__value__@@');
	}
?>

//feed
@feed = [data-v-feed]
@feed|deleteAllButFirst

@feed|before = <?php if (isset($this->seo['feed'])) foreach ($this->seo['feed'] as $name => $feed) {?>

	@feed a[data-v-feed-*]|href       = $feed['@@__data-v-feed-(*)__@@']
	@feed [data-v-feed-*]|innerText   = $feed['@@__data-v-feed-(*)__@@']
	@feed a[data-v-feed-edit]|onclick = <?php echo "openCodeEditorModal('/themes/{$feed['file']}', '{$feed['filename']}', 'js', 'themes')";?>
	@feed a[data-v-feed-edit]|href    = <?php echo 'javascript:void(0);';?>

@feed|after = <?php } ?>

//sitemap
@sitemap = [data-v-sitemap]
@sitemap|deleteAllButFirst

@sitemap|before = <?php if (isset($this->seo['sitemap'])) foreach ($this->seo['sitemap'] as $feed) {?>

	@sitemap a[data-v-sitemap-*]|href       = $feed['@@__data-v-sitemap-(*)__@@']
	@sitemap [data-v-sitemap-*]|innerText   = $feed['@@__data-v-sitemap-(*)__@@']
	@sitemap a[data-v-sitemap-edit]|onclick = <?php echo "openCodeEditorModal('/themes/{$feed['file']}', '{$feed['filename']}', 'js', 'themes')";?>
	@sitemap a[data-v-sitemap-edit]|href    = <?php echo 'javascript:void(0);';?>

@sitemap|after = <?php } ?>

//schema
@schema = [data-v-schema]
@schema|deleteAllButFirst

@schema|before = <?php $optgroup = '';if (isset($this->schema)) foreach ($this->schema as $name => $schema) {
			if ($name == 'none') continue;
			if (isset($schema['folder']) && ($optgroup != $schema['folder'])) {
				$optgroup = $schema['folder'];
				echo '</tbody><thead><th colspan="2">' . ucfirst($optgroup) . '</th></thead><tbody>';
			}
	?>
	
	@schema a[data-v-schema-*]|href       = $schema['@@__data-v-schema-(*)__@@']
	@schema [data-v-schema-*]|innerText   = $schema['@@__data-v-schema-(*)__@@']
	@schema a[data-v-schema-edit]|onclick = <?php echo "openCodeEditorModal('/plugins/seo/config/schemas/{$schema['file']}', '{$schema['filename']}', 'js', 'plugins')";?>
	@schema a[data-v-schema-edit]|href    = <?php echo 'javascript:void(0);';?>

@schema|after = <?php
/*
	if ($schema != $text['folder']) {
		$schema = $text['folder'];
		echo "/<optgroup>";
	}*/
} ?>


@post-type = [data-v-post-type]
@post-type|deleteAllButFirst

@post-type|before = <?php if (isset($this->seo['post-type'])) foreach ($this->seo['post-type'] as $type => $options) {?>

	@post-type a[data-v-post-type-*]|href = $type['@@__data-v-post-type-(*)__@@']
	@post-type [data-v-post-type-type]    = $type
	@post-type [data-v-post-type-name]    = <?php echo ucfirst($type);?>
	
	@post-type .schema-input|before = <?php foreach($options['schema'] ?? [] as $schemaIndex => $postSchema) {?>
	@post-type [data-v-seo-schema]|name = <?php echo "settings[post-type][$type][schema][$schemaIndex]"?>
	@post-type [data-v-seo-schema]|before = <?php
		//$selected = $options['schema'] ?? false;
		$selected = $postSchema ?? false;	
	?>
	@post-type .schema-input|after = <?php } ?>

@post-type|after = <?php } ?>

@product-type = [data-v-product-type]
@product-type|deleteAllButFirst

@product-type|before = <?php if (isset($this->seo['product-type'])) foreach ($this->seo['product-type'] as $type => $options) {?>

	@product-type a[data-v-product-type-*]|href = $type['@@__data-v-product-type-(*)__@@']
	@product-type [data-v-product-type-type]    = $type
	@product-type [data-v-product-type-name]    = <?php echo ucfirst($type);?>
	
	
	@product-type .schema-input|before = <?php foreach($options['schema'] ?? [] as $schemaIndex => $productSchema) {?>
	@product-type [data-v-seo-schema]|name   = <?php echo "settings[product-type][$type][schema][$schemaIndex]"?>
	@product-type [data-v-seo-schema]|before = <?php 
		$selected = $productSchema ?? false;	
	?>	
	@product-type .schema-input|after = <?php } ?>

@product-type|after = <?php } ?>



[data-v-seo-schema]|before = <?php $optgroup = '';?>
@schema-select-option = select[data-v-seo-schema] [data-v-option]
@schema-select-option|deleteAllButFirstChild

@schema-select-option|before = <?php
		if (isset($text['folder']) && ($optgroup != $text['folder'])) {
			$optgroup = $text['folder'];
			echo '<optgroup label="' . ucfirst($optgroup) . '">';
		}
?>

	@schema-select-option|value = <?php echo $text['file'];?>
	@schema-select-option = <?php echo ucfirst($text['title']);?>
	@schema-select-option|addNewAttribute = <?php if ($text['file'] == $selected) echo 'selected';?>
	
@schema-select-option|after = <?php
	if (isset($text['folder']) && ($optgroup != $text['folder'])) {
		$optgroup = $text['folder'];
		echo "/<optgroup>";
	}
?>

@route = [data-v-route]
@route|deleteAllButFirst

@route|before = <?php if (isset($this->routes)) foreach ($this->routes as $type => $options) {
	$route  = $this->seo['route'][$type] ?? [];
    $schema = $route['schema'] ?? [];
	?>

	@route a[data-v-route-*]|href     = $type['@@__data-v-route-(*)__@@']
	@route [data-v-route-type] = $type
	@route [data-v-route-name] = <?php echo ucfirst($type);?>
	@route [data-v-route-*]|innerText = $options['@@__data-v-route-(*)__@@']
	
	@route [data-v-route-title]|value = $route['title']
	@route [data-v-route-title]|name = <?php echo "settings[route][$type][title]"?>

	@route .schema-input|before = <?php foreach($schema as $schemaIndex => $routeSchema) {?>
	
	@route [data-v-seo-schema]|name = <?php echo "settings[route][$type][schema][$schemaIndex]"?>
	@route [data-v-seo-schema]|before = <?php
		//$selected = $route['schema'] ?? false;
		$selected = $routeSchema ?? false;	
	?>
	
	@route .schema-input|after = <?php } ?>	

@route|after = <?php } ?>