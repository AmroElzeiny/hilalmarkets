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

use function Vvveb\__;
use function Vvveb\getMultiPostContentMeta;
use function Vvveb\getMultiProductContentMeta;
use function Vvveb\getMultiSettingContent;
use function Vvveb\setMultiPostContentMeta;
use function Vvveb\setMultiProductContentMeta;
use function Vvveb\setMultiSettingContent;
use Vvveb\System\Core\Request;
use Vvveb\System\Core\View;
use Vvveb\System\Event;

if (! defined('V_VERSION')) {
	die('Invalid request!');
}

#[\AllowDynamicProperties]
class Admin {
	function __construct() {
		$this->view     = View::getInstance();

		$this->addSeoTabs();

		//add admin menu item
		$admin_path = \Vvveb\adminPath();
		Event::on('Vvveb\Controller\Base', 'init-menu', __CLASS__, function ($menu) use ($admin_path) {
			$menuItem = [
				'name'     => __('SEO'),
				'url'      => $admin_path . 'index.php?module=plugins/seo/settings',
				'module'   => 'plugins/seo/settings',
				//'icon-img' => PUBLIC_PATH . 'plugins/seo/seo.svg',
				'icon' => 'icon-search-outline',
			];

			$menu['plugins']['items']['seo-plugin'] = $menuItem;
			$menu['seo-plugin'] = $menuItem;

			return [$menu];
		});

		Event::on('Vvveb\Controller\Content\Edit', 'save', __CLASS__, [$this, 'save']);
		Event::on('Vvveb\Controller\Content\Edit', 'index', __CLASS__, [$this, 'getPost']);
		Event::on('Vvveb\Controller\Settings\Site', 'index', __CLASS__, [$this, 'getSite']);
		Event::on('Vvveb\Controller\Settings\Site', 'save', __CLASS__, [$this, 'saveSite']);

		// when plugin is installed first time run install and insert default settings
		Event::on('Vvveb\System\Extensions\Plugins', 'setup', __CLASS__, function ($pluginName, $siteId) {
			if ($pluginName == 'seo') {
				$this->install();
			}

			return [$pluginName, $siteId];
		});
	}

	function getPost($post, $post_id, $type) {
		$this->view->seo = $this->view->seo ?? [];

		if ($post_id) {
			if ($type == 'product') {
				$seo = getMultiProductContentMeta($post_id, 'seo') ?? [];
			} else {
				$seo = getMultiPostContentMeta($post_id, 'seo') ?? [];
			}

			foreach ($seo as $meta) {
				$this->view->seo[$meta['language_id']][$meta['key']] = $meta['value'];
			}
		}

		return [$post, $post_id, $type];
	}

	function getSite($site, $setting, $site_id, $data) {
		$this->view->seo = $this->view->seo ?? [];

		$seo = getMultiSettingContent($site_id, 'seo') ?? [];

		foreach ($seo as $meta) {
			$this->view->seo[$meta['language_id']][$meta['key']] = $meta['value'];
		}

		return [$site, $setting, $site_id, $data];
	}

	function save($post, $post_id, $type) {
		$request = Request::getInstance();
		$seo     = $request->post['seo'] ?? [];

		$meta = [];

		foreach ($seo as $key => $values) {
			foreach ($values as $language_id => $value) {
				if (is_array($value)) {
					$value = array_filter($value);
				}
				if ($value) {
					$meta[] = ['namespace' => 'seo', 'key' => $key, 'value' => $value, 'language_id' => $language_id];
				}
			}
		}

		if ($meta) {
			if ($type == 'product') {
				setMultiProductContentMeta($post_id, $meta);
			} else {
				setMultiPostContentMeta($post_id, $meta);
			}
		}

		return [$post, $post_id, $type];
	}

	function saveSite($site, $settings, $site_id, $data) {
		$request = Request::getInstance();
		$seo     = $request->post['seo'] ?? [];

		$meta = [];

		foreach ($seo as $key => $values) {
			foreach ($values as $language_id => $value) {
				if (is_array($value)) {
					$value = array_filter($value);
				}
				if ($value) {
					$meta[] = ['namespace' => 'seo', 'key' => $key, 'value' => $value, 'language_id' => $language_id];
				}
			}
		}

		if ($meta) {
			setMultiSettingContent($site_id, $meta);
		} else {
			\Vvveb\System\Meta\SettingContent::getInstance()->delete($site_id, 'seo', ['og', 'twitter']);
		}

		return [$site, $settings, $site_id, $data];
	}

	function addSeoTabs() {
		//add script on compile
		Event::on('Vvveb\System\Core\View', 'compile', __CLASS__, function ($template, $htmlFile, $tplFile, $vTpl, $view) {
			//insert js and css on post and product page
			if (($template == 'content' . DS . 'post.html') || ($template == 'product' . DS . 'product.html') || ($template == 'settings' . DS . 'site.html')) {
				//insert script
				$vTpl->loadTemplateFile(__DIR__ . DS . 'admin' . DS . 'template' . DS . 'seotab.tpl', true);
				//$vTpl->addCommand('body|append', $script);
			}

			return [$template, $htmlFile, $tplFile, $vTpl, $view];
		});
	}

	function install() {
		$install = new Install();
		$install->run();
	}
}
