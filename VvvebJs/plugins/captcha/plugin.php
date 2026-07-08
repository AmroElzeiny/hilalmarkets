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
Name: Captcha form protection
Slug: captcha
Category: spam
Url: https://plugins.vvveb.com/product/debug
Description: Add captcha to site forms: user registration, password reset, comments, reviews etc.
Author: givanz
Version: 0.1
Thumb: captcha.svg
Author url: https://www.vvveb.com
Settings: /admin/index.php?module=plugins/captcha/settings
*/

use function Vvveb\__;
use function Vvveb\getSetting;
use Vvveb\Plugins\Captcha\System\Turnstile;
use Vvveb\System\Core\View;
use Vvveb\System\Event;
use Vvveb\System\Routes;

if (! defined('V_VERSION')) {
	die('Invalid request!');
}

//use Vvveb\Plugins\CaptchaPlugin\Recaptcha;

class CaptchaPlugin {
	private $view;

	function checkCaptcha($data, $post = null) {
		$secret = getSetting('captcha', 'secret_key', null);
		$error  = false;

		if ($secret) {
			$turnstile = new Turnstile();

			if ($turnstile->validate($secret)) {
			} else {
				$error =  __('Invalid captcha!');
			}
		} else {
			$error =  __('Captcha key not set!');
		}

		if ($error) {
			$this->view->errors[]        = $error;
			$this->view->errors['login'] = $error;

			return [[], null];
		}

		return [$data, $post];
	}

	function admin() {
		// admin login
		Event::on('Vvveb\Controller\User\Login',  'index', __METHOD__ , [$this, 'checkCaptcha']);
	}

	function app() {
		$routes  = [
			'content/post/index',
			'content/page/index',
			'product/product/index',
			'user/login/index',
			'user/signup/index',
			'user/reset/index',
		];

		Event::on('Vvveb\System\Core\View', 'compile:after', __CLASS__, function ($template, $htmlFile, $tplFile, $vTpl, $view) use ($routes) {
			// add captcha js and field to page
			if ($url = Routes::getUrlData()) {
				if (in_array($url['module'], $routes)) {
					$vTpl->loadTemplateFile(__DIR__ . '/app/template/turnstile.tpl');
				}
			}
			
			$vTpl->loadTemplateFile(__DIR__ . '/app/template/common.tpl');

			return [$template, $htmlFile, $tplFile, $vTpl, $view];
		});

		// post comments
		Event::on('Vvveb\Controller\Content\Post', 'insertComment', __METHOD__ , [$this, 'checkCaptcha']);
		//product reviews, questions
		Event::on('Vvveb\Controller\Product\Product', 'insertComment', __METHOD__ , [$this, 'checkCaptcha']);
		//user signup
		Event::on('Vvveb\Controller\User\Signup', 'addUser', __METHOD__ , [$this, 'checkCaptcha']);
		// user password reset
		Event::on('Vvveb\Controller\User\Reset', 'index', __METHOD__ , [$this, 'checkCaptcha']);
		// user login
		Event::on('Vvveb\Controller\User\Login', 'login', __METHOD__ , [$this, 'checkCaptcha']);
		// contact form plugin
		Event::on('Vvveb\Plugins\ContactForm\Component\Form', 'request', __METHOD__ , [$this, 'checkCaptcha']);
	}

	function __construct() {
		$this->view     = View::getInstance();

		if (APP == 'admin') {
			//$this->admin();
		} else {
			if (APP == 'app') {
				$this->app();
			}
		}
	}
}

$captchaPlugin = new CaptchaPlugin();
