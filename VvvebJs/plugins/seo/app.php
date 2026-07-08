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

namespace Vvveb\Plugins\Seo;

use function Vvveb\getMultiPostContentMeta;
use function Vvveb\getMultiProductContentMeta;
use function Vvveb\getMultiSettingContent;
use function Vvveb\isEditor;
use function Vvveb\reconstructJson;
use Vvveb\System\Core\Request;
use Vvveb\System\Core\View;
use Vvveb\System\Event;
use Vvveb\System\Images;
use function Vvveb\truncateWords;

if (! defined('V_VERSION')) {
	die('Invalid request!');
}

#[\AllowDynamicProperties]
class App {
	function __construct() {
		$this->view    = View::getInstance();
		$this->request = Request::getInstance();

		if (isEditor()) {
			return;
		}

		Event::on('Vvveb\System\Core\View', 'compile:after', __CLASS__, function ($template, $htmlFile, $tplFile, $vTpl, $view) {
			$vTpl->loadTemplateFile(__DIR__ . '/app/template/common.tpl');

			return [$template, $htmlFile, $tplFile, $vTpl, $view];
		});

		$this->view->seo = \Vvveb\getSetting('seo');

		Event::on('Vvveb\Controller\Content\Post', 'index:after', __CLASS__, [$this, 'post']);
		Event::on('Vvveb\Controller\Content\Page', 'index:after', __CLASS__, [$this, 'page']);
		Event::on('Vvveb\Controller\Product\Product', 'index:after', __CLASS__, [$this, 'product']);
		Event::on('Vvveb\Controller\Content\Category', 'index:after', __CLASS__, [$this, 'taxonomy']);
		Event::on('Vvveb\Controller\Content\Tag', 'index:after', __CLASS__, [$this, 'taxonomy']);
		Event::on('Vvveb\Controller\Product\Category', 'index:after', __CLASS__, [$this, 'taxonomy']);
		Event::on('Vvveb\Controller\Product\Tag', 'index:after', __CLASS__, [$this, 'taxonomy']);
		//Event::on('Vvveb\Controller\Base', 'init:after', __CLASS__, [$this, 'site']);
		Event::on('Vvveb\System\Core\FrontController', 'call', __CLASS__, [$this, 'site']);
	}

	private function setSchema($schema) {
		$this->view->seo['schema'] = $this->view->seo['schema'] ?? [];
		$schemasDir = __DIR__ . '/config/schemas/';

		//get email html template
		foreach ($schema as $file) {
			$htmlView  = new View();
			$htmlView->setTheme();
			$vTpl = $htmlView->getTemplateEngineInstance();
			$vTpl->escapeInline = true;
			$htmlView->set(['seo' => $this->view->seo]);
			$htmlView->template($schemasDir . $file);
			$xml   = $htmlView->render(true, false, true);

			if ($xml) {
				$sxml  = \simplexml_load_string($xml, null, LIBXML_NOCDATA | LIBXML_DTDATTR);
				if (! $sxml) {
					error_log($xml);
				}
				$array = reconstructJson($sxml, true);
				$json  = json_encode($array, JSON_THROW_ON_ERROR | JSON_PRETTY_PRINT);

				if ($json) {
					$this->view->seo['schema'][$file] = $json;
				}
			}
		}
	}

	function site($template, $controller, $actionName) {
		//seo already set
		if (isset($this->view->seo)) {
			return;
		}
		
		$global = $controller->getGlobal();
		$status = \Vvveb\System\Core\FrontController::getStatus();

		if (isset($global['site_id']) && $status == 200) {
			$module = $this->request->get['module'] ?? 'index/index';
			
			$schema = $this->view->seo['route'][$module]['schema'] ?? []; //route type
			$schema += $this->view->seo['route']['*']['schema'] ?? []; //all pages

			$this->setSchema($schema);

			$seo  = [];
			$meta = getMultiSettingContent($global['site_id'], 'seo') ?? [];
			foreach ($meta as $item) {
				if ($item['language_id'] == $global['language_id']) {
					$seo[$item['key']] = $item['value'];
				}
			}

			$title = truncateWords($global['site']['description']['title'] ?? '',70);
			$description = truncateWords(strip_tags($global['site']['description']['description'] ?? ''), 160);
			$image = '';
			if ($global['site']['webbanner']) {
				$image = 'https://' . SITE_URL . $global['site']['webbanner'];
			}
			$seo += ['og' => [], 'twitter' => []];
			$seo['og'] += ['title' => $title, 'description' => $description, 'image' => $image, 'locale' => $global['code']];
			$seo['twitter'] += ['title' => $title, 'content' => $description, 'image' => $image];
			$global['site']['description']['meta-description'] = ($global['site']['description']['meta-description'] ?? '') ?: $description;

			$this->view->seo['meta'] = $seo;
		}

		return [$template, $controller, $actionName];
	}

	function pageType($pageType, $content, $languageContent, $language, $slug) {
		$post        = $content[$language] ?? [];
		$post_id     = $post[$pageType . '_id'] ?? false;
		$language_id = $post['language_id'] ?? false;

		if ($post_id) {
			$seo  = [];

			if ($pageType == 'post') {
				$meta = getMultiPostContentMeta($post_id, 'seo', null, [], $language_id) ?? [];
			} else {
				//$meta = getMultiProductContentMeta($post_id, 'seo', null, [], $language_id) ?? [];
				$meta = getMultiPostContentMeta($post_id, 'seo', null, [], $language_id) ?? [];
			}

			foreach ($meta as $item) {
				$seo[$item['key']] = $item['value'];
			}

			//add defaults for missing data
			$description = truncateWords(strip_tags($post['content']), 160);
			$image = '';
			if ($post['image']) {
				$image = 'https://' . SITE_URL . Images::image($post['image'], $pageType, 'medium');
			}
			$seo += ['og' => [], 'twitter' => []];
			$languageContent['meta_description'] = $languageContent['meta_description'] ?: $description;

			$seo['og'] += ['title' => $post['name'], 'description' => $description, 'image' => $image, 'locale' => $post['code']];
			$seo['twitter'] += ['title' => $post['name'], 'content' => $description, 'image' => $image];

			$this->view->seo['meta'] = $seo;
		}

		$postType = $post['type'] ?? '';
		$schema   = $this->view->seo[$pageType . '-type'][$postType]['schema'] ?? []; //post type
		$schema += $this->view->seo['route']['*']['schema'] ?? []; //all pages

		$this->setSchema($schema);

		return [$content, $languageContent, $language, $slug];
	}
	
	function taxonomy($taxonomy, $type) {
		$taxonomy_item_id = $taxonomy['taxonomy_item_id'] ?? false;

		if ($taxonomy_item_id) {
			$seo  = [];

			$description = truncateWords(strip_tags($taxonomy['content']), 160);
			$image = '';
			if ($taxonomy['image']) {
				$image = 'https://' . SITE_URL . Images::image($taxonomy['image'], 'category', 'medium');
			}
			$seo += ['og' => [], 'twitter' => []];
			$taxonomy['meta_description'] = $taxonomy['meta_description'] ?: $description;

			$seo['og'] += ['title' => $taxonomy['name'], 'description' => $description, 'image' => $image];
			$seo['twitter'] += ['title' => $taxonomy['name'], 'content' => $description, 'image' => $image];

			$this->view->seo['meta'] = $seo;
		}

		$postType = $post['type'] ?? '';
		$schema   = $this->view->seo['route']['/category/{slug}']['schema'] ?? [];
		$schema += $this->view->seo['route']['*']['schema'] ?? []; //all pages

		$this->setSchema($schema);

		return [$taxonomy, $type];
	}

	function page($content, $languageContent, $language, $slug) {
		return $this->pageType('post', $content, $languageContent, $language, $slug);
	}

	function post($content, $languageContent, $language, $slug) {
		return $this->pageType('post', $content, $languageContent, $language, $slug);
	}

	function product($content, $languageContent, $language, $slug) {
		return $this->pageType('product', $content, $languageContent, $language, $slug);
	}
}
