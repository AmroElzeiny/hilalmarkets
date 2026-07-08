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

namespace Vvveb\Plugins\TwoFactorAuth\Controller;

use function Vvveb\__;
use function Vvveb\session;
use function Vvveb\siteSettings;
use Vvveb\System\User\Admin;
use Vvveb\System\User\User as SystemUser;


class User extends Base {
	function index() {
		$type   = $this->request->get['type'] ?? 'admin';
		$user_id = $this->request->get[$type . '_id'] ?? $this->global[$type . '_id'] ?? false;

		if (Admin::hasCapability('view_other_admin')) {
		} else {
			if ($type == 'user' || ($user_id != $this->global['admin_id'])) {
				$message = __('Permission denied!');
				$view->errors[] = $message;
				$this->notFound($message, 403, true);
			}
		}

		if ($type == 'user') {
			$user   = SystemUser::get(['user_id' => $user_id]);
		} else {
			$user   = Admin::get(['admin_id' => $user_id]);
		}
		$secret  = $user['secret'];

		$this->view->enabled = $secret ? true : false;

		if (! $this->view->enabled) {
			$site  = siteSettings($this->global['site_id'], $this->global['language_id']);
			$title = $site['description']['title'] ?? 'Vvveb';

			$tfa = $this->init2fa($title);
			if (! $secret && ! ($secret = session('2fasecret'))) {
				$secret = $tfa->createSecret();
				session(['2fasecret' => $secret]);

				try {
					if (function_exists('socket_create')) {
						$tfa->ensureCorrectTime();
						$this->view->success[] = 'Your hosts time seems to be correct / within margin';
					}
				} catch (\RobThree\Auth\TwoFactorAuthException $ex) {
					$this->view->warning[] = 'Your server time seems to be off: ' . $ex->getMessage();
				}
			}

			$this->view->qrimage = $tfa->getQRCodeImageAsDataUri($user['email'], $secret);
			$this->view->secret  = chunk_split($secret, 4, ' ');
		}
	}

	function disable() {
		$editCapability = 'edit_other_admin';
		$type           = $this->request->get['type'] ?? 'admin';
		$user_id        = $this->request->get[$type . '_id'] ?? $this->global[$type . '_id'] ?? false;

		if (Admin::hasCapability($editCapability)) {
		} else {
			if ($type == 'user' || ($user_id != $this->global['admin_id'])) {
				$view->errors[] = __('Permission denied!');
				return;
			}
		}
		if ($type == 'user') {
			$user   = SystemUser::update(['secret' => ''],['user_id' => $user_id]);
		} else {
			$user   = Admin::update(['secret' => ''],['admin_id' => $user_id]);
		}

		$this->index();
	}

	function save() {
		$type   = $this->request->get['type'] ?? 'admin';
		$user_id  = $this->request->get[$type . '_id'] ?? $this->global[$type . '_id'] ?? false;

		$editCapability = 'edit_other_admin';
		if (Admin::hasCapability($editCapability)) {
		} else {
			if ($type == 'user' || ($user_id != $this->global['admin_id'])) {
				$view->errors[] = __('Permission denied!');
				return;
			}
		}

		$settings = $this->request->post['settings'] ?? [];
		$secret   = str_replace(' ', '', $settings['secret'] ?? '');
		$code     = str_replace(' ', '', $settings['code'] ?? '');

		$tfa = $this->init2fa();
		if ($tfa->verifyCode($secret, $code) === true) {
			if ($type == 'user') {
				$user = SystemUser::update(['secret' => $secret],['user_id' => $user_id]);
			} else {
				$user = Admin::update(['secret' => $secret],['admin_id' => $user_id]);
			}
		} else {
			$this->view->errors[] = __('Invalid code!');
		}

		$this->index();
	}
}
