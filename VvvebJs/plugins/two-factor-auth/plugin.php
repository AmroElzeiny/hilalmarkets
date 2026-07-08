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
Name: Two factor authentication
Slug: two-factor-auth
Category: performance
Url: https://www.vvveb.com
Description: Enhanced security with two factor authentication
Author: givanz
Version: 0.1
Thumb: two-factor-auth.svg
Author url: https://www.vvveb.com
Settings: /admin/index.php?module=plugins/two-factor-auth/user
*/

use function Vvveb\__;
use function Vvveb\isEditor;
use function Vvveb\session;
use Vvveb\System\Event;
use Vvveb\System\Routes;
use Vvveb\System\User\Admin;
use Vvveb\System\User\User;

if (! defined('V_VERSION')) {
	die('Invalid request!');
}

class TwoFactorAuthPlugin {
	function checkSecretAdmin() {
		$admin = Admin::current();
		$code  = session('2facode');

		if ($admin && $admin['secret'] && ! $code) {
			$redirect = \Vvveb\url(['module' => 'plugins/two-factor-auth/verify']);
			$module = $_GET['module'] ?? '';
			if ($module != 'plugins/two-factor-auth/verify') {
				header("Location: $redirect");
				die();
			}
		}
	}

	function checkSecret($site) {
		$user  = User::current();
		$admin = Admin::current();
		$code  = session('2facode');

		if ($user && $user['secret'] && ! $code && ! $admin) {
			$redirect = \Vvveb\url(['module' => 'plugins/two-factor-auth/verify']);
			$module = $_GET['module'] ?? '';
			if ($module != 'plugins/two-factor-auth/verify') {
				header("Location: $redirect");
				die();
			}
		}

		return [$site];
	}

	function admin() {
		//add admin menu item
		$admin_path = \Vvveb\adminPath();
		Event::on('Vvveb\Controller\Base', 'init-menu', __CLASS__, function ($menu) use ($admin_path) {
			$menu['plugins']['items']['two-factor-auth-plugin'] = [
				'name'     => __('Two factor auth'),
				'url'      => '/admin/',
				'icon-img' => PUBLIC_PATH . 'plugins/minify/two-factor-auth.svg',
			];

			return [$menu];
		});

		//Event::on('Vvveb\Controller\User\User',  'index:after', __METHOD__ , [$this, 'addSecret']);
		Event::on('Vvveb\Controller\Base',  'init', __METHOD__ , [$this, 'checkSecretAdmin']);

		//add script on compile
		Event::on('Vvveb\System\Core\View', 'compile', __CLASS__, function ($template, $htmlFile, $tplFile, $vTpl, $view) {
			//insert js and css on login page
			if ($template == 'admin' . DS . 'user.html' || $template == 'user' . DS . 'user.html') {
				//insert script
				$vTpl->loadTemplateFile(__DIR__ . '/admin/template/user-form.tpl');
			}

			return [$template, $htmlFile, $tplFile, $vTpl, $view];
		});
	}

	function app() {
		//don't load if page is opened in editor
		if (isEditor()) {
			return;
		}

		Routes::addRoute('/user/2fa', ['module' => 'plugins/two-factor-auth/index/index']);
		Routes::addRoute('/user/2fa/verify', ['module' => 'plugins/two-factor-auth/verify/index']);

		Event::on('Vvveb\Controller\Base',  'init', __METHOD__ , [$this, 'checkSecret']);
		Event::on('Vvveb\System\Core\View', 'compile:after', __CLASS__, function ($template, $htmlFile, $tplFile, $vTpl, $view) {
			$vTpl->loadTemplateFile(__DIR__ . '/app/template/common.tpl');

			return [$template, $htmlFile, $tplFile, $vTpl, $view];
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
	}
}

$twoFactorAuthPlugin = new TwoFactorAuthPlugin();
