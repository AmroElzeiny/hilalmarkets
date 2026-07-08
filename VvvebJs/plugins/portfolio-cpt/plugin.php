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

/*
Name: Portfolio Custom Post Type
Slug: portfolio-cpt
Category: content
Url: https://www.vvveb.com
Description: Adds portfolio custom post type and support for Portoflio type themes.
Author: givanz
Version: 0.1
Thumb: portfolio-cpt.svg
Author url: https://www.vvveb.com
*/

use function Vvveb\__;
use Vvveb\System\Event;

if (! defined('V_VERSION')) {
	die('Invalid request!');
}

class PortfolioCPTPlugin {
	function customProduct() {
		Event::on('Vvveb\Controller\Base', 'customPost', __CLASS__, function ($custom_posts) {
			$custom_posts += ['portfolio' => [
				'type'     => 'portfolio',
				'plural'   => 'portfolios',
				'icon'     => 'icon-grid-outline',
				'comments' => false,
			]];

			return [$custom_posts];
		});
	}

	function admin() {
		//add admin menu item
		$admin_path = \Vvveb\adminPath();
		Event::on('Vvveb\Controller\Base', 'init-menu', __CLASS__, function ($menu) use ($admin_path) {
			$menu['plugins']['items']['portfolio-cpt'] = [
				'name' => __('Portfolio items'),
				'url'  => $admin_path . 'index.php?module=plugins/portfolio-market/settings',
				'icon' => 'icon-grid-outline',
			];

			return [$menu];
		});

		//add portfolio custom product
		$this->customProduct();
	}

	function app() {
	}

	function __construct() {
		if (APP == 'admin') {
			$this->admin();
		} else {
			if (APP == 'app') {
				$this->app();
			}
		}
	}
}

$portfolioMarketPlugin = new PortfolioCPTPlugin();
