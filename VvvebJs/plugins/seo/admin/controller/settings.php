<?php

/**
 * Vvveb
 *
 * Copyright (C) 2022  Ziadin Givan
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU Affero General Public License as
 * published by the Free Software Foundation, either version 3 of the
 * License, or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU Affero General Public License for more details.
 *
 * You should have received a copy of the GNU Affero General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 *
 */

namespace Vvveb\Plugins\Seo\Controller;

use function Vvveb\__;
use Vvveb\Controller\Base;
use function Vvveb\getSetting;
use function Vvveb\globBrace;
use function Vvveb\humanReadable;
use function Vvveb\setMultiSetting;
use Vvveb\System\CacheManager;
use Vvveb\System\Event;
use Vvveb\System\Routes;
use Vvveb\System\Sites;
use function Vvveb\url;

class Settings extends Base {
	protected $defaultTypes = ['post' => [
		'post' => [
			'name'    => 'Post',
			'source'  => 'default',
			'site_id' => '0',
			'type'    => 'post',
			'plural'  => 'posts',
			'icon'    => 'icon-document-text-outline',
			'comments'=> true,
		],
		'page' => [
			'name'    => 'Page',
			'source'  => 'default',
			'site_id' => '0',
			'type'    => 'page',
			'plural'  => 'pages',
			'icon'    => 'icon-document-outline',
			'comments'=> false,
		],
	],
		'product' => [
			'product' => [
				'name'    => 'Product',
				'type'    => 'product',
				'site_id' => '0',
				'source'  => 'default',
				'plural'  => 'products',
				'icon'    => 'icon-cube-outline',
			],
		],
	];

	function save() {
		//$validator = new Validator(['plugins.gravatar.settings']);
		$settings  = $this->request->post['settings'] ?? false;
		$errors    = [];

		if ($settings /*&&
			($errors = $validator->validate($settings)) === true*/) {
			//$settings              = $validator->filter($settings);
			$results               = setMultiSetting('seo', $settings);
			//CacheManager::delete('component');
			CacheManager::delete();
			$this->view->success[] = __('Settings saved!');
		} else {
			$this->view->errors = $errors;
		}

		$this->index();
	}

	function schemas() {
		$pages   = [];
		$dir     = __DIR__ . '/../../config/schemas';

		//$files       = glob("$themeFolder/{,*/*/,*/}*.html", GLOB_BRACE);
		$glob        = ['', '*/*/', '*/'];
		$files       = globBrace($dir, $glob, '*.json');

		foreach ($files as $file) {
			$file     = preg_replace('@^.*/schemas/@', '', $file);
			$filename = basename($file);

			$folder   = \Vvveb\System\Functions\Str::match('@(\w+)/.*?$@', $file) ?? '';
			$path     = \Vvveb\System\Functions\Str::match('@(\w+)/.*?$@', $file);

			$name        = $title       = str_replace('.json', '', $filename);
			$description = '';
			$name        = ! empty($folder) ? "$folder-$name" : $name;

			if (isset($friendlyNames[$name])) {
				if (isset($friendlyNames[$name]['description'])) {
					$description = $friendlyNames[$name]['description'];
				}

				$title = $friendlyNames[$name]['name'];
			}

			$pages[$name]  = ['name' => $name, 'filename' => $filename, 'file' => $file, 'title' => humanReadable($title), 'folder' => $path, 'description' => $description];

			if (isset($friendlyNames[$name]['editor'])) {
				$pages[$name]['editor'] = $friendlyNames[$name]['editor'];
			}
		}

		krsort($pages);

		return $pages;
	}

	function feed($type = 'post') {
		$feed  = ['sitemap'=>[], 'feed' => []];
		$theme = Sites::getTheme(SITE_ID) ?? 'default';

		foreach (['default', $theme] as $t) {
			$dir   = DIR_THEMES . $t . DS . 'feed';

			if (is_dir($dir)) {
				$files = scandir($dir);

				foreach ($files as $filename) {
					if ($filename[0] == '.' || strpos($filename, '.xml') == false) {
						continue;
					}

					//$file     = preg_replace('@^.*/themes/@', '', $file);
					$file     = "$t/feed/$filename";
					$slug     = str_replace('.xml', '', $filename);
					$url      = url('feed/index', ['rss' => $slug]);

					if ($filename == 'index.xml') {
						$ns = 'sitemap';
					} else {
						$ns = 'feed';
					}

					$feed[$ns][$filename] = compact('slug', 'url', 'file', 'filename');
				}
			}
		}

		return $feed;
	}

	function postTypes($type = 'post') {
		$typeName          = ucfirst($type);
		list($pluginTypes) = Event::trigger('Vvveb\Controller\Base', "custom$typeName", []);
		array_walk($pluginTypes, function (&$type,$key) {
			$type['source'] = 'plugin';
			$type['name'] = ucfirst($key);
		});

		$userTypes = \Vvveb\getSetting($type, 'types', []);

		return $types = $this->defaultTypes[$type] + $pluginTypes + $userTypes;
	}

	function index() {
		$seo    = getSetting('seo') ?? [];
		$robots = DIR_PUBLIC . 'vrobots.txt';

		if (file_exists($robots)) {
			$seo['robots']['value'] = file_get_contents(DIR_PUBLIC . 'vrobots.txt') ?? '';
		}

		$seo['post-type']    = ($seo['post-type'] ?? []) + $this->postTypes('post');
		$seo['product-type'] = ($seo['product-type'] ?? []) + $this->postTypes('product');

		$this->view->schema = ['none' => ['title' => '[' . __('none') . ']', 'file' => '']] + $this->schemas(); //['article' => 'Article', 'product' => 'Product', 'recipe' => 'Recipe'];

		Routes::init();
		$routes             = Routes::getRoutes(); //['article' => 'Article', 'product' => 'Product', 'recipe' => 'Recipe'];

		unset($routes['/manifest.webmanifest']);

		foreach ($routes as $type => &$route) {
			$route['description'] = humanReadable($type);
		}
		$routes                     = ['*' => ['description' => __('All pages (default)')]] + $routes;
		$routes['/']['description'] = __('Homepage');
		$this->view->routes         = $routes;
		//$feed = $this->feed();
		$seo += $this->feed();

		$admin_path     = \Vvveb\adminPath();
		$controllerPath = $admin_path . 'index.php?module=editor/code';

		$this->view->saveUrl     = "$controllerPath&action=save";
		$this->view->loadFileUrl = "$controllerPath&action=loadFile";

		$this->view->seo = $seo;
	}
}
