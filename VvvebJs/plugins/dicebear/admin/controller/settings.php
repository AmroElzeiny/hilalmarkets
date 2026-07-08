<?php

/**
 * Vvveb
 *
 * Copyright (C) 2022  Ziadin Givan
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as
 * published by the Free Software Foundation, either version 3 of the
 * License, or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 *
 */

namespace Vvveb\Plugins\DiceBear\Controller;

use function Vvveb\__;
use Vvveb\Controller\Base;
use Vvveb\System\CacheManager;

class Settings extends Base {
	function save() {
		//$validator = new Validator(['plugins.gravatar.settings']);
		$settings  = $this->request->post['settings'] ?? false;
		$errors    = [];
		
		if ($settings /*&&
			($errors = $validator->validate($settings)) === true*/) {
			//$settings              = $validator->filter($settings);
			$results               = \Vvveb\setMultiSetting('dicebear', $settings);
			//CacheManager::delete('component');
			CacheManager::delete();
			$this->view->success[] = __('Settings saved!');
		} else {
			$this->view->errors = $errors;
		}
		
		$this->index();
	}

	function index() {
		$this->view->styles = ['adventurer-neutral', 'adventurer', 'avataaars-neutral', 'avataaars', 'big-ears-neutral', 'big-ears', 'big-smile', 'bottts-neutral', 'bottts', 'collection', 'converter', 'croodles-neutral', 'croodles', 'fun-emoji', 'icons', 'identicon', 'initials', 'lorelei-neutral', 'lorelei', 'micah', 'miniavs', 'notionists-neutral', 'notionists', 'open-peeps', 'personas', 'pixel-art-neutral', 'pixel-art', 'rings', 'shapes', 'thumbs'];
	}
}
