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
Name: Catalog mode
Slug: catalog-mode
Category: tools
Url: https://www.vvveb.com
Description: Hide add to cart and checkout and cart pages.
Author: givanz
Version: 0.1
Thumb: catalog-mode.svg
Author url: https://www.vvveb.com
*/

use Vvveb\System\Event;

if (! defined('V_VERSION')) {
	die('Invalid request!');
}

class CatalogModePlugin {
	function admin() {
	}

	function app() {
		$ecommerceRoutes = [
			//ecommerce
			//checkout
			'/cart',
			'/cart/add/#product_id#',
			'/cart/remove/#product_id#',
			'/cart/voucher',

			'/checkout/#product_id#',
			'/checkout',
			'/checkout/pay',
			'/checkout/confirm',
			'/checkout/confirm/#id#',
		];

		//remove ecommerce cart and checkout pages
		Event::on('Vvveb\System\Routes', 'init', __CLASS__, function ($routes) use ($ecommerceRoutes) {
			foreach ($ecommerceRoutes as $route) {
				unset($routes[$route]);
			}

			return [$routes];
		});
	}

	function __construct() {
		if (APP == 'admin') {
			$this->admin();
		} else {
			if (APP == 'app') {
				$this->app();
			}
		}

		Event::on('Vvveb\System\Core\View', 'compile:after', __CLASS__, function ($template, $htmlFile, $tplFile, $vTpl, $view) {
			//remove ecommerce components from html
			//if ($url = Routes::getUrlData()) {
			//if (in_array($url['route'], $routes)) {
			$vTpl->loadTemplateFile(__DIR__ . '/app/template/common.tpl');
			//}
			//}

			return [$template, $htmlFile, $tplFile, $vTpl, $view];
		});
	}
}

$hideEcommercePlugin = new CatalogModePlugin();
